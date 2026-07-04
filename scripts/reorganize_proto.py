"""Code-level reorganize prototype — no LLM.

Hypothesis: the Ableton-style reorganize task (rename + route + color
+ preservation) is rule-based enough that an LLM is overkill. This
script applies those rules deterministically against the bridge
subprocess and reports whether the existing harness invariants pass.

Run:
    uv run python -m scripts.reorganize_proto path/to/project.flp

If invariants pass, the same logic is a strong candidate for an MCP
tool kind (e.g. ``offline_execute`` kind ``reorganize_project``).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_CLI = REPO_ROOT / "flpdiff" / "src" / "cli.ts"


def _bridge_cmd() -> list[str]:
    bun = shutil.which("bun")
    if bun and BRIDGE_CLI.is_file():
        return [bun, "run", str(BRIDGE_CLI), "bridge"]
    flpdiff = shutil.which("flpdiff")
    if flpdiff:
        return [flpdiff, "bridge"]
    raise RuntimeError("no flpdiff bridge available — install bun or `npm i -g flpdiff`")


# ---------------------------------------------------------------------- #
# Classification rules. Order matters — earliest match wins.            #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class Group:
    key: str
    name: str  # canonical channel name
    rgb: tuple[int, int, int]


GROUPS: dict[str, Group] = {
    "kick": Group("drums_hard", "Kick", (233, 75, 60)),
    "snare": Group("drums_hard", "Snare", (233, 75, 60)),
    "clap": Group("drums_hard", "Clap", (233, 75, 60)),
    "rim": Group("drums_hard", "Rim", (233, 75, 60)),
    "hat": Group("drums_soft", "HiHat", (255, 140, 66)),
    "perc": Group("drums_soft", "Perc", (255, 140, 66)),
    "tom": Group("drums_soft", "Tom", (255, 140, 66)),
    "shaker": Group("drums_soft", "Shaker", (255, 140, 66)),
    "cymbal": Group("drums_soft", "Cymbal", (255, 140, 66)),
    "bass": Group("bass", "Bass", (59, 130, 246)),
    "sub": Group("bass", "Sub Bass", (59, 130, 246)),
    "808": Group("bass", "808", (59, 130, 246)),
    "lead": Group("lead", "Lead", (34, 197, 94)),
    "melody": Group("lead", "Melody", (34, 197, 94)),
    "arp": Group("lead", "Arp", (34, 197, 94)),
    "synth": Group("lead", "Synth", (34, 197, 94)),
    "piano": Group("lead", "Piano", (34, 197, 94)),
    "pad": Group("pad", "Pad", (139, 92, 246)),
    "string": Group("pad", "Strings", (139, 92, 246)),
    "atmos": Group("pad", "Atmos", (139, 92, 246)),
    "fx": Group("fx", "FX", (236, 72, 153)),
    "riser": Group("fx", "Riser", (236, 72, 153)),
    "sweep": Group("fx", "Sweep", (236, 72, 153)),
    "impact": Group("fx", "Impact", (236, 72, 153)),
    "vox": Group("vocal", "Vocal", (250, 204, 21)),
    "vocal": Group("vocal", "Vocal", (250, 204, 21)),
}

# Lookup keyword → Group. Compiled regex with word-boundaries.
KEYWORD_RE = {kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in GROUPS}


def _path_str(sample_path: Any) -> str:
    """Normalise the sample_path field — bridge surfaces ``{_type: path, value: str}`` or None."""
    if isinstance(sample_path, str):
        return sample_path
    if isinstance(sample_path, dict):
        v = sample_path.get("value")
        return v if isinstance(v, str) else ""
    return ""


def classify_channel(name: str | None, sample_path: Any) -> Group | None:
    """Return the matching Group or None for unknown content."""
    text = " ".join(filter(None, [name or "", _path_str(sample_path)]))
    if not text:
        return None
    for kw, regex in KEYWORD_RE.items():
        if regex.search(text):
            return GROUPS[kw]
    return None


def classify_by_pitch_range(notes: Iterable[dict[str, Any]]) -> Group | None:
    """Fallback for unnamed channels: bucket by average MIDI key."""
    keys = [n.get("key") for n in notes if isinstance(n.get("key"), int)]
    if not keys:
        return None
    avg = sum(keys) / len(keys)
    if avg < 48:
        return GROUPS["bass"]
    if avg < 72:
        return GROUPS["lead"]
    return GROUPS["pad"]


# ---------------------------------------------------------------------- #
# Bridge subprocess helper.                                              #
# ---------------------------------------------------------------------- #


def call_bridge(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Spawn `flpdiff bridge`, send {kind, args}, parse JSON response."""
    import json

    cmd = _bridge_cmd()
    request = json.dumps({"kind": kind, "args": args})
    proc = subprocess.run(
        cmd, input=request, capture_output=True, text=True, timeout=30, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bridge {kind} exit={proc.returncode}: {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"bridge {kind} returned empty stdout")
    return json.loads(out)


def _ok(envelope: dict[str, Any]) -> Any:
    if not envelope.get("ok"):
        msg = envelope.get("message") or envelope.get("error") or "?"
        raise RuntimeError(f"bridge {envelope.get('kind')!r} not ok: {msg}")
    result = envelope.get("result")
    return result if result is not None else {}


# ---------------------------------------------------------------------- #
# The reorganize algorithm.                                              #
# ---------------------------------------------------------------------- #


@dataclass
class ReorganizePlan:
    """The mutation list, computed before any writes happen."""

    channel_names: list[tuple[int, str]] = None  # type: ignore[assignment]
    channel_colors: list[tuple[int, tuple[int, int, int]]] = None  # type: ignore[assignment]
    channel_routings: list[tuple[int, int]] = None  # type: ignore[assignment]
    insert_names: list[tuple[int, str]] = None  # type: ignore[assignment]
    insert_colors: list[tuple[int, tuple[int, int, int]]] = None  # type: ignore[assignment]
    pattern_names: list[tuple[int, str]] = None  # type: ignore[assignment]
    pattern_colors: list[tuple[int, tuple[int, int, int]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        for fld in (
            "channel_names",
            "channel_colors",
            "channel_routings",
            "insert_names",
            "insert_colors",
            "pattern_names",
            "pattern_colors",
        ):
            if getattr(self, fld) is None:
                setattr(self, fld, [])

    def total(self) -> int:
        return sum(
            len(getattr(self, f))
            for f in (
                "channel_names",
                "channel_colors",
                "channel_routings",
                "insert_names",
                "insert_colors",
                "pattern_names",
                "pattern_colors",
            )
        )


def plan_reorganize(path: Path) -> ReorganizePlan:
    """Read project state, compute every mutation. No writes yet."""
    args = {"path": str(path)}
    describe = _ok(call_bridge("describe", args))
    patterns_raw = _ok(call_bridge("list_patterns", args))
    if isinstance(patterns_raw, list):
        patterns = patterns_raw
    elif isinstance(patterns_raw, dict):
        patterns = patterns_raw.get("patterns") or []
    else:
        patterns = []
    describe_channels: list[dict[str, Any]] = describe.get("channels") or []

    plan = ReorganizePlan()

    # 1. Classify each enabled channel + assign sequential inserts (1..N).
    classified: list[tuple[dict[str, Any], Group]] = []
    next_insert = 1
    for ch in describe_channels:
        if not ch.get("enabled", True):
            continue
        group = classify_channel(ch.get("name"), ch.get("sample_path"))
        if group is None:
            # Try MIDI-pitch fallback against patterns referencing this channel.
            fallback_notes: list[dict[str, Any]] = []
            for pat in patterns:
                for note in pat.get("notes") or []:
                    if note.get("channel_iid") == ch.get("iid") or note.get("channel") == ch.get(
                        "iid"
                    ):
                        fallback_notes.append(note)
            group = classify_by_pitch_range(fallback_notes) or GROUPS["synth"]
        classified.append((ch, group))

    # 2. Build mutation lists.
    used_names: dict[str, int] = {}
    for ch, group in classified:
        iid = ch.get("iid")
        if not isinstance(iid, int):
            continue
        # De-dup names for repeated content (Kick → Kick 2 → Kick 3).
        used_names[group.name] = used_names.get(group.name, 0) + 1
        n = used_names[group.name]
        unique_name = group.name if n == 1 else f"{group.name} {n}"

        plan.channel_names.append((iid, unique_name))
        plan.channel_colors.append((iid, group.rgb))
        plan.channel_routings.append((iid, next_insert))
        plan.insert_names.append((next_insert, unique_name))
        plan.insert_colors.append((next_insert, group.rgb))
        next_insert += 1

    # 3. Color patterns by the group of the dominant channel they reference.
    iid_to_group: dict[int, Group] = {
        ch["iid"]: g for ch, g in classified if isinstance(ch.get("iid"), int)
    }
    for pat in patterns:
        pid = pat.get("iid") or pat.get("id")
        if not isinstance(pid, int):
            continue
        notes = pat.get("notes") or []
        votes: dict[str, int] = {}
        for note in notes:
            ch_iid = note.get("channel_iid", note.get("channel"))
            if isinstance(ch_iid, int) and ch_iid in iid_to_group:
                g = iid_to_group[ch_iid]
                votes[g.key] = votes.get(g.key, 0) + 1
        if not votes:
            continue
        winning_key = max(votes, key=lambda k: votes[k])
        winner = next(g for g in GROUPS.values() if g.key == winning_key)
        existing_name = pat.get("name") or ""
        if not existing_name or re.match(r"^Pattern\s*\d*$", existing_name, re.IGNORECASE):
            plan.pattern_names.append((pid, winner.name))
        plan.pattern_colors.append((pid, winner.rgb))

    return plan


def apply_plan(path: Path, plan: ReorganizePlan) -> None:
    """Send the plan as a sequence of bridge calls."""
    args_template = {"path": str(path)}

    for iid, name in plan.channel_names:
        _ok(call_bridge("set_channel_name", {**args_template, "iid": iid, "name": name}))
    for iid, rgb in plan.channel_colors:
        _ok(
            call_bridge(
                "set_channel_color",
                {**args_template, "iid": iid, "color": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}},
            )
        )
    for iid, target in plan.channel_routings:
        _ok(
            call_bridge(
                "set_channel_routing", {**args_template, "iid": iid, "target_insert": target}
            )
        )
    for idx, name in plan.insert_names:
        _ok(call_bridge("set_insert_name", {**args_template, "index": idx, "name": name}))
    for idx, rgb in plan.insert_colors:
        _ok(
            call_bridge(
                "set_insert_color",
                {**args_template, "index": idx, "color": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}},
            )
        )
    for pid, name in plan.pattern_names:
        _ok(call_bridge("set_pattern_name", {**args_template, "iid": pid, "name": name}))
    for pid, rgb in plan.pattern_colors:
        _ok(
            call_bridge(
                "set_pattern_color",
                {**args_template, "iid": pid, "color": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}},
            )
        )


# ---------------------------------------------------------------------- #
# CLI + invariant verification                                           #
# ---------------------------------------------------------------------- #


async def _capture_state(path: Path):
    sys.path.insert(0, str(REPO_ROOT / "mcp"))
    from tests.e2e.harness.client import open_session
    from tests.e2e.harness.state_capture import capture_state

    async with open_session(REPO_ROOT / "mcp" / ".tmp_proto") as ses:
        return await capture_state(ses, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("flp", type=Path)
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args()

    if not args.flp.exists():
        print(f"missing: {args.flp}", file=sys.stderr)
        return 2

    workdir = REPO_ROOT / "mcp" / ".tmp_proto"
    workdir.mkdir(exist_ok=True)
    scratch = workdir / args.flp.name
    shutil.copy2(args.flp, scratch)

    print(f"input: {args.flp}")
    print(f"scratch: {scratch}")

    t0 = time.monotonic()
    plan = plan_reorganize(scratch)
    t_plan = time.monotonic() - t0
    print(f"plan: {plan.total()} mutations ({t_plan * 1000:.0f} ms)")
    print(
        f"  channel_names: {len(plan.channel_names)}, colors: {len(plan.channel_colors)}, routings: {len(plan.channel_routings)}"
    )
    print(f"  insert_names: {len(plan.insert_names)}, colors: {len(plan.insert_colors)}")
    print(f"  pattern_names: {len(plan.pattern_names)}, colors: {len(plan.pattern_colors)}")

    t0 = time.monotonic()
    apply_plan(scratch, plan)
    t_apply = time.monotonic() - t0
    print(f"apply: {t_apply:.2f}s")

    if args.no_verify:
        return 0

    # Run the existing harness invariants for parity with LLM run.
    from tests.e2e.harness.invariants import REORGANIZE_INVARIANTS, InvariantContext, run_invariants

    before = asyncio.run(_capture_state(args.flp))
    after = asyncio.run(_capture_state(scratch))
    bun = shutil.which("bun")
    flpdiff_cmd = [bun, "run", str(BRIDGE_CLI)] if (bun and BRIDGE_CLI.is_file()) else ["flpdiff"]
    ctx = InvariantContext(
        before=before,
        after=after,
        before_path=args.flp,
        after_path=scratch,
        flpdiff_cmd=flpdiff_cmd,
    )
    report = run_invariants(REORGANIZE_INVARIANTS, ctx)
    print()
    print(report.summary())
    print()
    print(f"hard pass: {report.passed_hard}")
    return 0 if report.passed_hard else 1


if __name__ == "__main__":
    sys.exit(main())
