"""Invariant validators for the e2e harness.

Pure-Python, no Anthropic dep. Reads pre/post state from FLPState
captures (see ``state_capture.py``) and runs a list of pass/fail checks.

Hard invariants (kind="hard") gate the test — any failure fails the
test. Soft invariants are informational, surfaced in the report but
don't gate.

The agent never sees these — they read fresh state to prevent the
agent from gaming the validator by lying in tool returns.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .state_capture import FLPState

InvariantKind = Literal["hard", "soft"]


@dataclass
class InvariantResult:
    passed: bool
    detail: str
    remediation: str | None = None


@dataclass
class Invariant:
    name: str
    kind: InvariantKind
    predicate: Callable[[InvariantContext], InvariantResult]


@dataclass
class InvariantContext:
    """Bundle the inputs every invariant predicate may need."""

    before: FLPState
    after: FLPState
    before_path: Path
    after_path: Path
    flpdiff_cmd: list[str] = field(default_factory=lambda: ["flpdiff"])


@dataclass
class InvariantReport:
    results: list[tuple[Invariant, InvariantResult]]

    @property
    def hard_failures(self) -> list[tuple[Invariant, InvariantResult]]:
        return [(inv, res) for inv, res in self.results if inv.kind == "hard" and not res.passed]

    @property
    def passed_hard(self) -> bool:
        return not self.hard_failures

    def summary(self) -> str:
        lines = []
        for inv, res in self.results:
            mark = "PASS" if res.passed else "FAIL"
            lines.append(f"  [{mark}] ({inv.kind}) {inv.name}: {res.detail}")
        return "\n".join(lines)


def run_invariants(invariants: list[Invariant], ctx: InvariantContext) -> InvariantReport:
    results: list[tuple[Invariant, InvariantResult]] = []
    for inv in invariants:
        try:
            res = inv.predicate(ctx)
        except Exception as exc:
            res = InvariantResult(passed=False, detail=f"predicate raised {exc!r}")
        results.append((inv, res))
    return InvariantReport(results=results)


# ---------------------------------------------------------------------- #
# Reorganize-flow invariants                                             #
# ---------------------------------------------------------------------- #

# Default-name patterns FL emits for fresh / unedited objects. If any
# track / channel / insert / pattern still matches one of these
# post-reorganize, the agent didn't actually rename it.
DEFAULT_NAME_RE = re.compile(
    r"^(Sample|Track|Insert|Channel|Pattern|Audio Track|Insert\s)\s*\d*$",
    re.IGNORECASE,
)

# Groups + their canonical palette hex (0xAARRGGBB ints). Matches the
# table at mcp/docs/examples/03-reorganize-project.md:117-126.
PALETTE: dict[str, int] = {
    "drums_hard": 0xFFE94B3C,
    "drums_soft": 0xFFFF8C42,
    "bass": 0xFF3B82F6,
    "lead": 0xFF22C55E,
    "pad": 0xFF8B5CF6,
    "fx": 0xFFEC4899,
    "vocal": 0xFFFACC15,
}
PALETTE_VALUES: set[int] = set(PALETTE.values())
# Bridge writes 0xAARRGGBB but ``list_*`` reads back with alpha=0 in
# the {r,g,b,a} dict shape (FL stores user colors with alpha bit
# unset). Compare on the lower 24 bits so palette membership matches
# the bridge round-trip rather than the ideal-world canonical form.
PALETTE_RGB_VALUES: set[int] = {v & 0x00FFFFFF for v in PALETTE_VALUES}


def _color_int(value: object) -> int | None:
    """Coerce a color field (int / dict / hex string) to its 0xAARRGGBB int.

    The bridge surfaces colors as either raw uints or
    ``{r, g, b, a}`` dicts depending on kind — accept both.
    """
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return int(value, 16) & 0xFFFFFFFF
        except ValueError:
            return None
    if isinstance(value, dict):
        try:
            a = int(value.get("a", 0xFF))
            r = int(value["r"])
            g = int(value["g"])
            b = int(value["b"])
        except (KeyError, TypeError, ValueError):
            return None
        return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
    return None


def _named_objects(state: FLPState) -> list[tuple[str, str]]:
    """Return (object_kind, name) for every named user-facing object."""
    out: list[tuple[str, str]] = []
    for ch in state.channels:
        name = ch.get("name") or ch.get("default_name")
        if name:
            out.append(("channel", str(name)))
    for ins in state.mixer:
        name = ins.get("name") or ins.get("default_name")
        if name:
            out.append(("insert", str(name)))
    for pat in state.patterns:
        name = pat.get("name") or pat.get("default_name")
        if name:
            out.append(("pattern", str(name)))
    for tr in state.tracks:
        name = tr.get("name") or tr.get("default_name")
        if name:
            out.append(("track", str(name)))
    return out


def _semantic_names(ctx: InvariantContext) -> InvariantResult:
    bad = [(k, n) for k, n in _named_objects(ctx.after) if DEFAULT_NAME_RE.match(n.strip())]
    if not bad:
        return InvariantResult(passed=True, detail="all user-facing objects have semantic names")
    sample = ", ".join(f"{k}:{n!r}" for k, n in bad[:5])
    return InvariantResult(
        passed=False,
        detail=f"{len(bad)} object(s) still default-named (e.g. {sample})",
        remediation="rename via set_channel_name / set_insert_name / set_pattern_name / set_track_name",
    )


def _routing_distinct_non_master(ctx: InvariantContext) -> InvariantResult:
    """Every active channel routed to a non-Master insert; routings distinct.

    The bridge's ``list_channels`` summary omits routing; the
    ``describe`` payload's ``channels[].target_insert`` is the
    authoritative source.
    """
    routes: list[tuple[int, int]] = []  # (channel_iid, target_insert)
    describe_channels = ctx.after.describe.get("channels") or []
    by_iid = {c.get("iid"): c for c in describe_channels if isinstance(c, dict)}
    for ch in ctx.after.channels:
        if not ch.get("enabled", True):
            continue
        iid = ch.get("iid", ch.get("id"))
        target: int | None = None
        # 1. Authoritative: describe.channels[iid].target_insert.
        full = by_iid.get(iid, {})
        if isinstance(full.get("target_insert"), int):
            target = full["target_insert"]
        # 2. Fallback: list_channels-shaped {iid, routing|target_insert}.
        if target is None and isinstance(ch.get("target_insert"), int):
            target = ch["target_insert"]
        if target is None:
            r = ch.get("routing")
            if isinstance(r, int):
                target = r
            elif isinstance(r, dict):
                inner = r.get("target_insert", r.get("target"))
                if isinstance(inner, int):
                    target = inner
        if target is not None and isinstance(iid, int):
            routes.append((iid, target))

    if not routes:
        return InvariantResult(
            passed=False,
            detail="no enabled channels carry a routing field",
            remediation="set_channel_routing(iid, target_insert) for each active channel",
        )
    on_master = [iid for iid, t in routes if t in (0, -1)]
    if on_master:
        return InvariantResult(
            passed=False,
            detail=f"{len(on_master)} channel(s) still on Master/auto: iids={on_master[:5]}",
            remediation="route each active channel to its own dedicated insert (target != 0)",
        )
    targets = [t for _, t in routes]
    duplicates = [t for t in set(targets) if targets.count(t) > 1]
    if duplicates:
        return InvariantResult(
            passed=False,
            detail=f"{len(duplicates)} insert(s) hosting multiple channels: {duplicates[:5]}",
            remediation="give each channel its own insert (one-to-one)",
        )
    return InvariantResult(
        passed=True, detail=f"{len(routes)} channels routed to distinct non-Master inserts"
    )


def _palette_colors(ctx: InvariantContext) -> InvariantResult:
    """Every set color drawn from the canonical palette."""
    off_palette: list[tuple[str, int]] = []
    for kind, items in (
        ("channel", ctx.after.channels),
        ("insert", ctx.after.mixer),
        ("pattern", ctx.after.patterns),
        ("track", ctx.after.tracks),
    ):
        for obj in items:
            color = _color_int(obj.get("color"))
            if color is None or color == 0:
                continue
            if (color & 0x00FFFFFF) not in PALETTE_RGB_VALUES:
                off_palette.append((kind, color))
    if not off_palette:
        return InvariantResult(passed=True, detail="all set colors drawn from palette")
    sample = ", ".join(f"{k}:0x{c:08X}" for k, c in off_palette[:5])
    return InvariantResult(
        passed=False,
        detail=f"{len(off_palette)} object(s) using off-palette colors (e.g. {sample})",
        remediation=f"use one of: {[hex(v) for v in sorted(PALETTE_VALUES)]}",
    )


def _clips_preserved(ctx: InvariantContext) -> InvariantResult:
    before = len(ctx.before.clips)
    after = len(ctx.after.clips)
    if before == after:
        return InvariantResult(passed=True, detail=f"clip count preserved ({before})")
    return InvariantResult(
        passed=False,
        detail=f"clip count changed: before={before} after={after}",
        remediation="reorganize must not add/remove clips — only move/rename/recolor",
    )


def _notes_preserved(ctx: InvariantContext) -> InvariantResult:
    """Per-pattern note count must not change during reorganize."""

    def per_pattern_notes(state: FLPState) -> dict[int, int]:
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

    before = per_pattern_notes(ctx.before)
    after = per_pattern_notes(ctx.after)
    diffs = {
        iid: (before[iid], after.get(iid, 0)) for iid in before if before[iid] != after.get(iid, 0)
    }
    if not diffs:
        return InvariantResult(
            passed=True,
            detail=f"per-pattern note counts unchanged across {len(before)} patterns",
        )
    return InvariantResult(
        passed=False,
        detail=f"{len(diffs)} pattern(s) with note count drift: {dict(list(diffs.items())[:3])}",
        remediation="reorganize must not edit notes — only metadata + routing",
    )


def _flpdiff_clean(ctx: InvariantContext) -> InvariantResult:
    """flpdiff <A> <B> exits 0 or 1 (never 2 = parse error).

    The CLI takes two paths positionally with no subcommand for diff.
    """
    cmd = [*ctx.flpdiff_cmd, str(ctx.before_path), str(ctx.after_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError:
        return InvariantResult(
            passed=False,
            detail=f"flpdiff binary not found: {' '.join(cmd[:1])}",
            remediation="install flpdiff or set ctx.flpdiff_cmd",
        )
    except subprocess.TimeoutExpired:
        return InvariantResult(passed=False, detail="flpdiff diff timed out (30s)")
    if proc.returncode == 2:
        return InvariantResult(
            passed=False,
            detail=f"flpdiff diff returned 2 (error): {proc.stderr.strip()[:200]}",
            remediation="post-reorganize FLP failed to parse — encoder produced invalid bytes",
        )
    return InvariantResult(
        passed=True,
        detail=f"flpdiff diff exited cleanly (rc={proc.returncode})",
    )


def _channels_untouched(ctx: InvariantContext) -> InvariantResult:
    """No channel name / color / routing changed before↔after.

    v2 reorganize is playlist-only: it must leave the channel rack
    byte-identical. Compares against `describe.channels[]` since list_*
    omits routing.
    """
    before = ctx.before.describe.get("channels") or []
    after = ctx.after.describe.get("channels") or []
    if len(before) != len(after):
        return InvariantResult(
            passed=False,
            detail=f"channel count changed ({len(before)} → {len(after)})",
        )
    diffs: list[str] = []
    for b, a in zip(before, after, strict=False):
        for fld in ("iid", "name", "color", "target_insert", "sample_path"):
            if b.get(fld) != a.get(fld):
                diffs.append(f"iid={b.get('iid')}.{fld}: {b.get(fld)!r} → {a.get(fld)!r}")
                break
    if diffs:
        return InvariantResult(
            passed=False,
            detail=f"{len(diffs)} channel(s) modified: {diffs[:3]}",
            remediation="reorganize must not mutate channels",
        )
    return InvariantResult(passed=True, detail=f"all {len(before)} channels byte-identical")


def _inserts_untouched(ctx: InvariantContext) -> InvariantResult:
    """No mixer insert name / color changed before↔after.

    Bridge surfaces inserts as ``describe.mixer.inserts[]``; we extract
    the same list from before/after.
    """

    def _inserts_of(d: dict) -> list:
        mix = d.get("mixer")
        if isinstance(mix, dict):
            ins = mix.get("inserts")
            if isinstance(ins, list):
                return ins
        ins = d.get("inserts")
        return ins if isinstance(ins, list) else []

    before = _inserts_of(ctx.before.describe)
    after = _inserts_of(ctx.after.describe)
    if len(before) != len(after):
        return InvariantResult(
            passed=False,
            detail=f"insert count changed ({len(before)} → {len(after)})",
        )
    diffs: list[str] = []
    for b, a in zip(before, after, strict=False):
        for fld in ("name", "color"):
            if b.get(fld) != a.get(fld):
                diffs.append(f"idx={b.get('index')}.{fld}")
                break
    if diffs:
        return InvariantResult(
            passed=False,
            detail=f"{len(diffs)} insert(s) modified: {diffs[:3]}",
            remediation="reorganize must not mutate mixer",
        )
    return InvariantResult(passed=True, detail=f"all {len(before)} inserts byte-identical")


def _patterns_untouched(ctx: InvariantContext) -> InvariantResult:
    """No pattern name / color changed before↔after (notes/length covered separately)."""
    before = ctx.before.describe.get("patterns") or []
    after = ctx.after.describe.get("patterns") or []
    if len(before) != len(after):
        return InvariantResult(
            passed=False,
            detail=f"pattern count changed ({len(before)} → {len(after)})",
        )
    diffs: list[str] = []
    for b, a in zip(before, after, strict=False):
        for fld in ("iid", "id", "name", "color"):
            if b.get(fld) != a.get(fld):
                diffs.append(f"iid={b.get('iid', b.get('id'))}.{fld}")
                break
    if diffs:
        return InvariantResult(
            passed=False,
            detail=f"{len(diffs)} pattern(s) modified: {diffs[:3]}",
            remediation="reorganize must not mutate patterns",
        )
    return InvariantResult(passed=True, detail=f"all {len(before)} patterns byte-identical")


def _tracks_named_per_family(ctx: InvariantContext) -> InvariantResult:
    """At least one named playlist track exists in the after-state.

    Soft check — when there are no clips, no tracks need naming, so
    an empty after.tracks is also a pass.
    """
    after_tracks = ctx.after.tracks
    after_clips = ctx.after.clips
    if not after_clips:
        return InvariantResult(passed=True, detail="no clips → no tracks expected to be named")
    palette_rgb = {(g["rgb_r"], g["rgb_g"], g["rgb_b"]) for g in _PALETTE_RGBS}
    named_count = 0
    palette_count = 0
    for tr in after_tracks:
        name = (tr.get("name") or "").strip()
        if name:
            named_count += 1
        c = _color_int(tr.get("color"))
        if c is not None:
            rgb = ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
            if rgb in palette_rgb:
                palette_count += 1
    if named_count == 0:
        return InvariantResult(
            passed=False,
            detail="no playlist tracks were named after reorganize",
            remediation="reorganize must produce at least one named track when clips exist",
        )
    return InvariantResult(
        passed=True,
        detail=f"{named_count} named track(s), {palette_count} palette-colored",
    )


_PALETTE_RGBS = [
    {"rgb_r": (v >> 16) & 0xFF, "rgb_g": (v >> 8) & 0xFF, "rgb_b": v & 0xFF}
    for v in PALETTE_RGB_VALUES
]


REORGANIZE_INVARIANTS: list[Invariant] = [
    Invariant("channels_untouched", "hard", _channels_untouched),
    Invariant("inserts_untouched", "hard", _inserts_untouched),
    Invariant("patterns_untouched", "hard", _patterns_untouched),
    Invariant("clips_preserved", "hard", _clips_preserved),
    Invariant("notes_preserved", "hard", _notes_preserved),
    Invariant("tracks_named_per_family", "soft", _tracks_named_per_family),
    Invariant("flpdiff_clean", "hard", _flpdiff_clean),
]
