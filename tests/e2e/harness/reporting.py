"""Per-run artifact dump + auto-pruning.

After every e2e test, write everything a human (or a future debugging
session) might want: transcript, before/after FLP info, flpdiff diff,
invariant report, judge verdict, run metadata.

Last 20 runs are kept; older auto-pruned.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent import AgentRun
from .client import HarnessSession
from .invariants import InvariantReport
from .judge import JudgeVerdict
from .state_capture import FLPState

DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
RUN_RETENTION = 20


def _to_jsonable(obj: Any) -> Any:
    """Recursively coerce dataclasses / Path / non-JSON to plain types."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")


def _flpdiff_info(flp_path: Path, *, flpdiff_cmd: list[str]) -> dict[str, Any] | str:
    """Run `flpdiff info <path> --format json` and parse the output."""
    cmd = [*flpdiff_cmd, "info", str(flp_path), "--format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError:
        return f"flpdiff not found: {' '.join(cmd[:1])}"
    if proc.returncode != 0:
        return f"flpdiff exit={proc.returncode}: {proc.stderr.strip()[:300]}"
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout[:2000]


def _flpdiff_diff(before: Path, after: Path, *, flpdiff_cmd: list[str]) -> str:
    # flpdiff CLI takes two FLP paths positionally; no diff subcommand.
    cmd = [*flpdiff_cmd, "--verbose", str(before), str(after)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError:
        return f"flpdiff not found: {' '.join(cmd[:1])}"
    return (
        f"$ {' '.join(cmd)}\n[exit {proc.returncode}]\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )


def write_artifacts(
    *,
    test_id: str,
    runs_dir: Path | None = None,
    session: HarnessSession,
    run: AgentRun,
    invariants: InvariantReport,
    judge: JudgeVerdict | None,
    before_path: Path,
    after_path: Path,
    before_state: FLPState | None = None,
    after_state: FLPState | None = None,
    flpdiff_cmd: list[str] | None = None,
) -> Path:
    """Write a complete artifact bundle for one test run; return the dir."""
    base = runs_dir if runs_dir is not None else DEFAULT_RUNS_DIR
    base.mkdir(parents=True, exist_ok=True)

    run_dir = base / f"{_now_stamp()}-{test_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    flpdiff = flpdiff_cmd or ["flpdiff"]

    # Transcript: one tool call per JSONL line.
    with (run_dir / "transcript.jsonl").open("w", encoding="utf-8") as f:
        for entry in session.transcript:
            f.write(json.dumps(_to_jsonable(entry), ensure_ascii=False) + "\n")

    # Agent turns + token totals.
    (run_dir / "agent_run.json").write_text(
        json.dumps(_to_jsonable(run), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # FLP info before/after.
    (run_dir / "before.flp.info.json").write_text(
        json.dumps(_flpdiff_info(before_path, flpdiff_cmd=flpdiff), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "after.flp.info.json").write_text(
        json.dumps(_flpdiff_info(after_path, flpdiff_cmd=flpdiff), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # State captures (the validator's view).
    if before_state is not None:
        (run_dir / "before.state.json").write_text(
            json.dumps(_to_jsonable(before_state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if after_state is not None:
        (run_dir / "after.state.json").write_text(
            json.dumps(_to_jsonable(after_state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # flpdiff diff.
    (run_dir / "diff.txt").write_text(
        _flpdiff_diff(before_path, after_path, flpdiff_cmd=flpdiff),
        encoding="utf-8",
    )

    # Invariant report.
    (run_dir / "invariants.json").write_text(
        json.dumps(
            {
                "passed_hard": invariants.passed_hard,
                "results": [
                    {
                        "name": inv.name,
                        "kind": inv.kind,
                        "passed": res.passed,
                        "detail": res.detail,
                        "remediation": res.remediation,
                    }
                    for inv, res in invariants.results
                ],
                "summary": invariants.summary(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Judge verdict.
    if judge is not None:
        (run_dir / "judge.json").write_text(
            json.dumps(_to_jsonable(judge), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Meta: token totals, timing, termination.
    meta = {
        "test_id": test_id,
        "iterations": run.iterations,
        "terminated": run.terminated,
        "elapsed_s": run.elapsed_s,
        "input_tokens": run.total_input_tokens,
        "output_tokens": run.total_output_tokens,
        "cache_creation_tokens": run.total_cache_creation,
        "cache_read_tokens": run.total_cache_read,
        "tool_call_count": len(session.transcript),
        "judge_grade": judge.grade if judge else None,
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _prune_runs(base)
    return run_dir


def _prune_runs(base: Path, retention: int = RUN_RETENTION) -> None:
    """Keep newest N run dirs; delete the rest."""
    if not base.is_dir():
        return
    runs = [p for p in base.iterdir() if p.is_dir()]
    if len(runs) <= retention:
        return
    runs.sort(key=lambda p: p.name, reverse=True)  # ISO timestamp prefix sorts correctly
    for old in runs[retention:]:
        shutil.rmtree(old, ignore_errors=True)
