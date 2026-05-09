"""Invariants for the full-song-edit flow (Flow 2).

The agent's job is to *add* musical content. We don't care how it
gets there (single `add_pattern_note` calls vs. one `set_pattern_notes`
batch); we only verify the post-state delta. Hard invariants:

- Total note count grew by at least the case's `min_notes_added`.
- All notes still parse cleanly via flpdiff (never exit code 2).
- No existing pattern *lost* notes (agent shouldn't accidentally wipe).
"""

from __future__ import annotations

import subprocess
from typing import Any

from .invariants import (
    Invariant,
    InvariantContext,
    InvariantResult,
)


def _total_notes(state: Any) -> int:
    total = 0
    for pat in state.patterns:
        notes = pat.get("notes")
        if isinstance(notes, list):
            total += len(notes)
        elif isinstance(notes, int):
            total += notes
    return total


def _per_pattern_notes(state: Any) -> dict[int, int]:
    counts: dict[int, int] = {}
    for pat in state.patterns:
        iid = pat.get("iid") or pat.get("id")
        if not isinstance(iid, int):
            continue
        notes = pat.get("notes")
        if isinstance(notes, list):
            counts[iid] = len(notes)
        elif isinstance(notes, int):
            counts[iid] = notes
    return counts


def make_notes_added_invariant(min_added: int) -> Invariant:
    """Build a hard invariant: post.total_notes - pre.total_notes >= min_added."""

    def predicate(ctx: InvariantContext) -> InvariantResult:
        before = _total_notes(ctx.before)
        after = _total_notes(ctx.after)
        delta = after - before
        if delta >= min_added:
            return InvariantResult(
                passed=True,
                detail=f"{delta} notes added (>= {min_added}); total {before} -> {after}",
            )
        return InvariantResult(
            passed=False,
            detail=f"only {delta} notes added; expected >= {min_added} (total {before} -> {after})",
            remediation="agent must call add_pattern_note / set_pattern_notes enough times to satisfy the case",
        )

    return Invariant(name=f"notes_added_>={min_added}", kind="hard", predicate=predicate)


def _no_existing_notes_lost(ctx: InvariantContext) -> InvariantResult:
    before = _per_pattern_notes(ctx.before)
    after = _per_pattern_notes(ctx.after)
    losses = {
        iid: (before[iid], after.get(iid, 0)) for iid in before if after.get(iid, 0) < before[iid]
    }
    if not losses:
        return InvariantResult(
            passed=True,
            detail=f"no existing pattern lost notes ({len(before)} patterns checked)",
        )
    return InvariantResult(
        passed=False,
        detail=f"{len(losses)} pattern(s) lost notes: {dict(list(losses.items())[:3])}",
        remediation="agent shouldn't replace notes in existing patterns; only add",
    )


def _flpdiff_parses(ctx: InvariantContext) -> InvariantResult:
    """Post-FLP must round-trip through flpdiff cleanly (exit 0/1, never 2)."""
    cmd = [*ctx.flpdiff_cmd, str(ctx.before_path), str(ctx.after_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 2:
        return InvariantResult(
            passed=False,
            detail=f"flpdiff exit 2 (parse error): {proc.stderr[:200]}",
            remediation="post-flp file is corrupt; an offline mutation produced an invalid byte sequence",
        )
    return InvariantResult(
        passed=True,
        detail=f"flpdiff parsed clean (exit {proc.returncode})",
    )


def make_expect_new_pattern_invariant() -> Invariant:
    def predicate(ctx: InvariantContext) -> InvariantResult:
        before_ids = {p.get("iid") or p.get("id") for p in ctx.before.patterns}
        after_ids = {p.get("iid") or p.get("id") for p in ctx.after.patterns}
        new = after_ids - before_ids
        if new:
            return InvariantResult(passed=True, detail=f"new pattern(s) created: {sorted(new)}")
        return InvariantResult(
            passed=False,
            detail="no new pattern; expected create_pattern call",
            remediation="agent must call create_pattern when the prompt asks for a new pattern",
        )

    return Invariant(name="new_pattern_created", kind="hard", predicate=predicate)


def make_expect_new_channel_invariant() -> Invariant:
    def predicate(ctx: InvariantContext) -> InvariantResult:
        before_iids = {c.get("iid") for c in ctx.before.channels}
        after_iids = {c.get("iid") for c in ctx.after.channels}
        new = after_iids - before_iids
        if new:
            return InvariantResult(passed=True, detail=f"new channel(s) created: {sorted(new)}")
        return InvariantResult(
            passed=False,
            detail="no new channel; expected create_channel call",
            remediation="agent must call create_channel when the prompt asks for a new channel",
        )

    return Invariant(name="new_channel_created", kind="hard", predicate=predicate)


# Base invariant set every full-song case runs.
FULL_SONG_BASE_INVARIANTS: list[Invariant] = [
    Invariant(name="no_existing_notes_lost", kind="hard", predicate=_no_existing_notes_lost),
    Invariant(name="flpdiff_parses_clean", kind="hard", predicate=_flpdiff_parses),
]


def build_invariants(
    *,
    min_notes_added: int,
    expect_new_pattern: bool = False,
    expect_new_channel: bool = False,
) -> list[Invariant]:
    """Compose the per-case invariant list from a case's flags."""
    out: list[Invariant] = list(FULL_SONG_BASE_INVARIANTS)
    out.append(make_notes_added_invariant(min_notes_added))
    if expect_new_pattern:
        out.append(make_expect_new_pattern_invariant())
    if expect_new_channel:
        out.append(make_expect_new_channel_invariant())
    return out
