"""Stress-test ``reorganize_project`` across the full local corpus.

Iterates every ``.flp`` under ``flpdiff/tests/corpus/local/``, copies
each to a temporary scratch dir, runs the new ``offline_execute(kind=
"reorganize_project")`` against the *copy*, and runs the harness
invariants on the result. Originals are read-only — every mutation
lands in a tempdir that is cleaned up on exit.

Reports per-FLP pass/fail, classifier accuracy stats (how many
channels matched by keyword vs fell back to pitch vs hit the default
"Synth" bucket), wall-clock per call, and a final summary.

Usage:
    uv run python -m scripts.reorganize_stress
    uv run python -m scripts.reorganize_stress --limit 10  # subset
    uv run python -m scripts.reorganize_stress --filter "*bass*"
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "flpdiff" / "tests" / "corpus" / "local"
FLPDIFF_CLI = REPO_ROOT / "flpdiff" / "src" / "cli.ts"

sys.path.insert(0, str(REPO_ROOT / "mcp"))
from tests.e2e.harness.client import open_session  # noqa: E402
from tests.e2e.harness.invariants import (  # noqa: E402
    REORGANIZE_INVARIANTS,
    InvariantContext,
    run_invariants,
)
from tests.e2e.harness.state_capture import capture_state  # noqa: E402


@dataclass
class CaseResult:
    name: str
    bytes: int
    elapsed_ms: float
    mutations_applied: int
    plan_channels: int
    plan_inserts: int
    plan_patterns: int
    hard_pass: bool
    failed_invariants: list[str]
    error: str | None = None


def _classify_plan(plan: dict[str, Any]) -> Counter[str]:
    """How many channels landed in each group? Audit the classifier."""
    counts: Counter[str] = Counter()
    for ch in plan.get("channels") or []:
        # plan.channels[].rgb already encodes group; bucket by name.
        n = ch.get("name", "")
        counts[n] += 1
    return counts


async def run_case(
    flp: Path,
    scratch_root: Path,
) -> CaseResult:
    """Copy `flp` to scratch, run reorganize_project, run invariants."""
    case_dir = scratch_root / flp.stem
    case_dir.mkdir(parents=True, exist_ok=True)
    scratch = case_dir / flp.name
    shutil.copy2(flp, scratch)
    bytes_before = scratch.stat().st_size

    try:
        async with open_session(case_dir / "sess") as ses:
            before = await capture_state(ses, scratch)
            t0 = time.monotonic()
            env = await ses.call_tool(
                "offline_execute",
                {"kind": "reorganize_project", "args": {"path": str(scratch)}},
            )
            elapsed = (time.monotonic() - t0) * 1000.0
            if not env.get("ok"):
                msg = (env.get("result") or {}).get("message") or "?"
                return CaseResult(
                    name=flp.name,
                    bytes=bytes_before,
                    elapsed_ms=elapsed,
                    mutations_applied=0,
                    plan_channels=0,
                    plan_inserts=0,
                    plan_patterns=0,
                    hard_pass=False,
                    failed_invariants=["bridge_error"],
                    error=msg,
                )
            result = env["result"]
            plan = result.get("plan", {})
            after = await capture_state(ses, scratch)

        ctx = InvariantContext(
            before=before,
            after=after,
            before_path=flp,
            after_path=scratch,
            flpdiff_cmd=["bun", "run", str(FLPDIFF_CLI)],
        )
        report = run_invariants(REORGANIZE_INVARIANTS, ctx)
        failed = [inv.name for inv, res in report.results if not res.passed and inv.kind == "hard"]
        return CaseResult(
            name=flp.name,
            bytes=bytes_before,
            elapsed_ms=elapsed,
            mutations_applied=int(result.get("mutations_applied") or 0),
            plan_channels=len(plan.get("channels") or []),
            plan_inserts=len(plan.get("inserts") or []),
            plan_patterns=len(plan.get("patterns") or []),
            hard_pass=report.passed_hard,
            failed_invariants=failed,
        )
    except Exception as exc:
        return CaseResult(
            name=flp.name,
            bytes=bytes_before,
            elapsed_ms=0,
            mutations_applied=0,
            plan_channels=0,
            plan_inserts=0,
            plan_patterns=0,
            hard_pass=False,
            failed_invariants=["exception"],
            error=repr(exc),
        )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--filter", default=None)
    parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    parser.add_argument(
        "--keep-scratch", action="store_true", help="Don't delete tmp scratch dir on exit"
    )
    args = parser.parse_args()

    if not args.corpus.is_dir():
        print(f"corpus dir missing: {args.corpus}", file=sys.stderr)
        return 2

    flps = sorted(args.corpus.rglob("*.flp"))
    if args.filter:
        flps = [f for f in flps if fnmatch.fnmatch(f.name, args.filter)]
    if args.limit:
        flps = flps[: args.limit]
    if not flps:
        print("no FLPs matched", file=sys.stderr)
        return 1

    print(f"corpus: {args.corpus}")
    print(f"running on {len(flps)} FLP(s)")
    print()

    scratch_root_ctx = tempfile.TemporaryDirectory(prefix="reorg_stress_")
    if args.keep_scratch:
        scratch_root = Path(tempfile.mkdtemp(prefix="reorg_stress_"))
        print(f"scratch (kept): {scratch_root}")
    else:
        scratch_root = Path(scratch_root_ctx.name)

    results: list[CaseResult] = []
    try:
        for i, flp in enumerate(flps, 1):
            r = await run_case(flp, scratch_root)
            results.append(r)
            mark = "PASS" if r.hard_pass else "FAIL"
            extra = ""
            if r.failed_invariants:
                extra = f" failed=[{','.join(r.failed_invariants)}]"
            if r.error:
                extra += f" err={r.error[:80]}"
            print(
                f"  [{i:3d}/{len(flps)}] [{mark}] {r.name:<45s} "
                f"{r.bytes/1024:6.0f} KiB  {r.elapsed_ms:6.0f} ms  "
                f"chans={r.plan_channels:3d}  pats={r.plan_patterns:3d}  "
                f"muts={r.mutations_applied:3d}{extra}"
            )
    finally:
        if not args.keep_scratch:
            scratch_root_ctx.cleanup()

    # Summary.
    n = len(results)
    pass_count = sum(1 for r in results if r.hard_pass)
    fail_count = n - pass_count
    avg_ms = sum(r.elapsed_ms for r in results) / n if n else 0
    median_ms = sorted(r.elapsed_ms for r in results)[n // 2] if n else 0

    failed_buckets: Counter[str] = Counter()
    for r in results:
        for f in r.failed_invariants:
            failed_buckets[f] += 1

    print()
    print("=" * 80)
    print(f"PASS: {pass_count}/{n}   FAIL: {fail_count}")
    print(f"latency: avg {avg_ms:.0f} ms,  median {median_ms:.0f} ms")
    if failed_buckets:
        print()
        print("failure breakdown:")
        for name, count in failed_buckets.most_common():
            print(f"  {name:35s} x {count}")
    if n - pass_count > 0:
        print()
        print("failed cases:")
        for r in results:
            if not r.hard_pass:
                print(
                    f"  - {r.name}: {','.join(r.failed_invariants)}{f' / {r.error[:120]}' if r.error else ''}"
                )

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
