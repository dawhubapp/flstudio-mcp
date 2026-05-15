"""Invariants for the full-song-edit flow (Flow 2).

The agent's job is to *add* musical content. We don't care how it
gets there (single `add_pattern_note` calls vs. one `set_pattern_notes`
batch); we only verify the post-state delta. Hard invariants:

- Total note count grew by at least the case's `min_notes_added`.
- All notes still parse cleanly via flpdiff (never exit code 2).
- No existing pattern *lost* notes (agent shouldn't accidentally wipe).
- Optional: a specific native-plugin parameter byte changed and
  decodes into an expected normalized [0,1] range.
"""

from __future__ import annotations

import struct
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

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


def _total_controllers(state: Any) -> int:
    total = 0
    for pat in state.patterns:
        ctrls = pat.get("controllers")
        if isinstance(ctrls, list):
            total += len(ctrls)
        elif isinstance(ctrls, int):
            total += ctrls
    return total


def make_controllers_added_invariant(min_added: int) -> Invariant:
    """Hard invariant: post.total_controllers - pre.total_controllers >= min_added.

    Counts pattern-scoped controllers (0xDF, decoded by the parser
    into PatternSummary.controllers) across every pattern. Mirrors
    `make_notes_added_invariant`.
    """

    def predicate(ctx: InvariantContext) -> InvariantResult:
        before = _total_controllers(ctx.before)
        after = _total_controllers(ctx.after)
        delta = after - before
        if delta >= min_added:
            return InvariantResult(
                passed=True,
                detail=f"{delta} controllers added (>= {min_added}); total {before} -> {after}",
            )
        return InvariantResult(
            passed=False,
            detail=(
                f"only {delta} controllers added; expected >= {min_added} "
                f"(total {before} -> {after})"
            ),
            remediation=(
                "agent must call add_pattern_controller / set_pattern_controllers "
                "enough times to satisfy the case"
            ),
        )

    return Invariant(name=f"controllers_added_>={min_added}", kind="hard", predicate=predicate)


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


# ---------------------------------------------------------------------- #
# Plugin parameter byte-level invariants                                 #
# ---------------------------------------------------------------------- #


@dataclass
class PluginParamExpectation:
    """Assert one native-plugin parameter changed into an expected range.

    Locate the plugin's `0xD5` state blob in the post-FLP raw bytes
    (search by UTF-16LE name + match against `blob_size`), decode
    the bytes at `offset` per `field_type`, normalize to [0,1], and
    assert in `[min_normalized, max_normalized]`.

    `scale` is required for `field_type == "i32_bipolar"`.
    """

    plugin_name: str
    blob_size: int
    offset: int
    field_type: Literal["u8", "u16", "u32", "i32_bipolar"]
    min_normalized: float
    max_normalized: float
    scale: int | None = None


def _find_plugin_blob(flp_bytes: bytes, plugin_name: str, expected_size: int) -> bytes | None:
    """Search FLP for a 0xD5 blob immediately following ``plugin_name``
    (UTF-16LE NUL-terminated) and matching ``expected_size`` bytes.

    Walks forward from the name match scanning for `0xD5 + varint
    length + payload`. Returns the payload bytes or None if no
    match.
    """
    needle = plugin_name.encode("utf-16le") + b"\x00\x00"
    idx = flp_bytes.find(needle)
    if idx < 0:
        return None
    p = idx
    end = len(flp_bytes) - 5
    while p < end:
        if flp_bytes[p] == 0xD5:
            length = 0
            shift = 0
            q = p + 1
            try:
                while True:
                    b = flp_bytes[q]
                    length |= (b & 0x7F) << shift
                    q += 1
                    if (b & 0x80) == 0:
                        break
                    shift += 7
            except IndexError:
                return None
            payload = flp_bytes[q : q + length]
            if length == expected_size:
                return payload
        p += 1
    return None


def _decode_param_normalized(blob: bytes, exp: PluginParamExpectation) -> float | None:
    """Decode the bytes at ``exp.offset`` per ``exp.field_type`` into [0,1].

    Inverse of the encoder in flpdiff/src/mutations/index.ts:
    - u8:  raw / 0xFF
    - u16: raw / 0xFFFF (LE)
    - u32: raw / 0xFFFFFFFF (LE)
    - i32_bipolar: (signed/scale + 1) / 2 (LE)
    """
    if exp.offset < 0:
        return None
    end = exp.offset + (1 if exp.field_type == "u8" else (2 if exp.field_type == "u16" else 4))
    if end > len(blob):
        return None
    if exp.field_type == "u8":
        return blob[exp.offset] / 0xFF
    if exp.field_type == "u16":
        raw = struct.unpack_from("<H", blob, exp.offset)[0]
        return raw / 0xFFFF
    if exp.field_type == "u32":
        raw = struct.unpack_from("<I", blob, exp.offset)[0]
        return raw / 0xFFFFFFFF
    if exp.field_type == "i32_bipolar":
        if exp.scale is None or exp.scale == 0:
            return None
        signed = struct.unpack_from("<i", blob, exp.offset)[0]
        return (signed / exp.scale + 1) / 2
    return None


def make_plugin_param_changed_invariant(exp: PluginParamExpectation) -> Invariant:
    name = (
        f"plugin_param_changed:{exp.plugin_name}@0x{exp.offset:x}"
        f"[{exp.min_normalized:.2f},{exp.max_normalized:.2f}]"
    )

    def predicate(ctx: InvariantContext) -> InvariantResult:
        before_bytes = ctx.before_path.read_bytes()
        after_bytes = ctx.after_path.read_bytes()
        before_blob = _find_plugin_blob(before_bytes, exp.plugin_name, exp.blob_size)
        after_blob = _find_plugin_blob(after_bytes, exp.plugin_name, exp.blob_size)
        if before_blob is None:
            return InvariantResult(
                passed=False,
                detail=f"plugin {exp.plugin_name!r} blob not found in BEFORE FLP",
                remediation="case fixture must already contain the target plugin",
            )
        if after_blob is None:
            return InvariantResult(
                passed=False,
                detail=f"plugin {exp.plugin_name!r} blob not found in AFTER FLP",
                remediation="agent's mutations may have corrupted or removed the plugin's 0xD5 blob",
            )
        before_val = _decode_param_normalized(before_blob, exp)
        after_val = _decode_param_normalized(after_blob, exp)
        if before_val is None or after_val is None:
            return InvariantResult(
                passed=False,
                detail=f"could not decode param at offset 0x{exp.offset:x} ({exp.field_type})",
                remediation="check expectation offset/field_type matches the layout in flpdiff/src/mutations/index.ts",
            )
        if abs(after_val - before_val) < 1e-6:
            return InvariantResult(
                passed=False,
                detail=(
                    f"param at 0x{exp.offset:x} unchanged ({before_val:.4f}); "
                    f"expected change into [{exp.min_normalized:.2f}, {exp.max_normalized:.2f}]"
                ),
                remediation="agent must call set_native_plugin_param with the requested target value",
            )
        if not (exp.min_normalized <= after_val <= exp.max_normalized):
            return InvariantResult(
                passed=False,
                detail=(
                    f"param at 0x{exp.offset:x} = {after_val:.4f} outside expected "
                    f"[{exp.min_normalized:.2f}, {exp.max_normalized:.2f}] (was {before_val:.4f})"
                ),
                remediation="agent set the plugin param but to a value outside the requested range",
            )
        return InvariantResult(
            passed=True,
            detail=f"param at 0x{exp.offset:x} changed {before_val:.4f} -> {after_val:.4f}",
        )

    return Invariant(name=name, kind="hard", predicate=predicate)


@dataclass
class ChannelLevelExpectation:
    """Assert a channel's volume or pan landed in an expected normalized range.

    Reads `state.channels[iid].levels.{volume,pan}` from the AFTER
    state (already exposed by `list_channels`), normalizes per the
    raw scale, and asserts in `[min_normalized, max_normalized]`.
    Volume scale: raw / 12800. Pan scale: raw / 6400 (bipolar).
    """

    iid: int
    field: Literal["volume", "pan"]
    min_normalized: float
    max_normalized: float


def make_channel_level_invariant(exp: ChannelLevelExpectation) -> Invariant:
    name = (
        f"channel_level:iid={exp.iid}.{exp.field}"
        f"[{exp.min_normalized:.2f},{exp.max_normalized:.2f}]"
    )
    scale = 12800 if exp.field == "volume" else 6400

    def predicate(ctx: InvariantContext) -> InvariantResult:
        ch = next((c for c in ctx.after.channels if c.get("iid") == exp.iid), None)
        if ch is None:
            return InvariantResult(
                passed=False,
                detail=f"channel iid={exp.iid} not found in AFTER state",
            )
        levels = ch.get("levels")
        if not isinstance(levels, dict):
            return InvariantResult(
                passed=False,
                detail=f"channel iid={exp.iid} has no levels dict",
            )
        raw = levels.get(exp.field)
        if not isinstance(raw, int | float):
            return InvariantResult(
                passed=False,
                detail=f"levels.{exp.field} not numeric ({raw!r})",
            )
        normalized = raw / scale
        if not (exp.min_normalized <= normalized <= exp.max_normalized):
            return InvariantResult(
                passed=False,
                detail=(
                    f"channel iid={exp.iid} {exp.field}={normalized:.4f} (raw {raw}) "
                    f"outside [{exp.min_normalized:.2f}, {exp.max_normalized:.2f}]"
                ),
                remediation="agent must call set_channel_volume / set_channel_pan with target value",
            )
        return InvariantResult(
            passed=True,
            detail=f"channel iid={exp.iid} {exp.field}={normalized:.4f} (raw {raw})",
        )

    return Invariant(name=name, kind="hard", predicate=predicate)


@dataclass
class ChannelSamplePathExpectation:
    """Assert a channel's sample_path changed from baseline AND
    optionally that the new path matches a substring (e.g. '707').
    """

    iid: int
    must_differ: bool = True
    new_substring: str | None = None  # case-insensitive substring match on new path


def make_channel_sample_path_invariant(exp: ChannelSamplePathExpectation) -> Invariant:
    name = f"channel_sample_path:iid={exp.iid}"
    if exp.new_substring:
        name += f"~{exp.new_substring!r}"

    def predicate(ctx: InvariantContext) -> InvariantResult:
        before = next((c for c in ctx.before.channels if c.get("iid") == exp.iid), None)
        after = next((c for c in ctx.after.channels if c.get("iid") == exp.iid), None)
        if after is None:
            return InvariantResult(
                passed=False,
                detail=f"channel iid={exp.iid} missing from AFTER state",
            )
        before_path = before.get("sample_path") if before else None
        after_path = after.get("sample_path")
        if not after_path or not isinstance(after_path, str):
            return InvariantResult(
                passed=False,
                detail=f"channel iid={exp.iid} has no sample_path in AFTER state",
                remediation="agent must call set_channel_sample_path with a non-empty FL token",
            )
        if exp.must_differ and after_path == before_path:
            return InvariantResult(
                passed=False,
                detail=f"channel iid={exp.iid} sample_path unchanged ({after_path!r})",
                remediation="agent must change the sample_path, not leave it as the baseline",
            )
        if exp.new_substring and exp.new_substring.lower() not in after_path.lower():
            return InvariantResult(
                passed=False,
                detail=(
                    f"channel iid={exp.iid} sample_path={after_path!r} "
                    f"does not contain expected substring {exp.new_substring!r}"
                ),
            )
        return InvariantResult(
            passed=True,
            detail=f"channel iid={exp.iid} sample_path: {before_path!r} -> {after_path!r}",
        )

    return Invariant(name=name, kind="hard", predicate=predicate)


@dataclass
class VisualGateExpectation:
    """One question for the visual-acceptance gate.

    The gate flow (see ``visual_gate.run_visual_gates``):
      1. autodrive opens the post-mutation FLP in FL Studio.
      2. screencapture of FL's frontmost window.
      3. Claude vision call answers ``question`` PASS/FAIL.

    Soft assertion — failures are surfaced to the judge + sidecar
    JSON but don't fail the test by default. Gated on
    ``FLSTUDIO_VISUAL_GATE=1`` because it requires FL running, an
    interactive macOS session, ~30 s wall time, and ~$0.01 in API
    cost per gate.
    """

    question: str
    name: str | None = None  # short label for the report; defaults to question[:48]
    must_pass: bool = False  # if True the gate is HARD; surfaces as test failure
    full_screen: bool = False  # capture entire screen instead of FL window only


def build_invariants(
    *,
    min_notes_added: int,
    min_controllers_added: int = 0,
    expect_new_pattern: bool = False,
    expect_new_channel: bool = False,
    expect_plugin_params: list[PluginParamExpectation] | None = None,
    expect_channel_levels: list[ChannelLevelExpectation] | None = None,
    expect_channel_sample_paths: list[ChannelSamplePathExpectation] | None = None,
) -> list[Invariant]:
    """Compose the per-case invariant list from a case's flags."""
    out: list[Invariant] = list(FULL_SONG_BASE_INVARIANTS)
    out.append(make_notes_added_invariant(min_notes_added))
    if min_controllers_added > 0:
        out.append(make_controllers_added_invariant(min_controllers_added))
    if expect_new_pattern:
        out.append(make_expect_new_pattern_invariant())
    if expect_new_channel:
        out.append(make_expect_new_channel_invariant())
    for exp in expect_plugin_params or []:
        out.append(make_plugin_param_changed_invariant(exp))
    for exp in expect_channel_levels or []:
        out.append(make_channel_level_invariant(exp))
    for exp in expect_channel_sample_paths or []:
        out.append(make_channel_sample_path_invariant(exp))
    return out
