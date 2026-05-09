"""Unit tests for the full-song-flow invariant predicates.

Feeds canned ``FLPState`` dicts to ``invariants_song.build_invariants``
and asserts the expected pass/fail. Runs in normal CI without an API
key. The ``flpdiff_parses_clean`` invariant calls the CLI; tests skip
that one (covered by the e2e tests' actual round-trip).
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from ..harness.invariants import InvariantContext
from ..harness.invariants_song import (
    PluginParamExpectation,
    _decode_param_normalized,
    _find_plugin_blob,
    _no_existing_notes_lost,
    build_invariants,
    make_controllers_added_invariant,
    make_expect_new_channel_invariant,
    make_expect_new_pattern_invariant,
    make_notes_added_invariant,
    make_plugin_param_changed_invariant,
)
from ..harness.state_capture import FLPState


def _state(
    *,
    channels: list[dict[str, Any]] | None = None,
    patterns: list[dict[str, Any]] | None = None,
) -> FLPState:
    return FLPState(
        path=Path("/tmp/fake.flp"),
        describe={"ppq": 96},
        channels=channels or [],
        patterns=patterns or [],
    )


def _ctx(before: FLPState, after: FLPState) -> InvariantContext:
    return InvariantContext(
        before=before,
        after=after,
        before_path=Path("/tmp/before.flp"),
        after_path=Path("/tmp/after.flp"),
    )


# ----------------------------------------------------------- notes_added #


def test_notes_added_pass():
    inv = make_notes_added_invariant(min_added=4)
    before = _state(patterns=[{"id": 1, "notes": [{"key": 60}]}])
    after = _state(patterns=[{"id": 1, "notes": [{"key": 60}, *[{"key": 36}] * 4]}])
    result = inv.predicate(_ctx(before, after))
    assert result.passed
    assert "4 notes added" in result.detail


def test_notes_added_fail_too_few():
    inv = make_notes_added_invariant(min_added=4)
    before = _state(patterns=[{"id": 1, "notes": [{"key": 60}]}])
    after = _state(patterns=[{"id": 1, "notes": [{"key": 60}, {"key": 36}, {"key": 38}]}])
    result = inv.predicate(_ctx(before, after))
    assert not result.passed
    assert "only 2 notes added" in result.detail


def test_notes_added_handles_int_count_field():
    """state_capture may emit notes as int count rather than list."""
    inv = make_notes_added_invariant(min_added=2)
    before = _state(patterns=[{"id": 1, "notes": 1}])
    after = _state(patterns=[{"id": 1, "notes": 4}])
    result = inv.predicate(_ctx(before, after))
    assert result.passed


# ------------------------------------------------------- no_existing_notes_lost #


def test_no_existing_notes_lost_pass():
    before = _state(patterns=[{"id": 1, "notes": [{"key": 60}, {"key": 62}]}])
    after = _state(patterns=[{"id": 1, "notes": [{"key": 60}, {"key": 62}, {"key": 64}]}])
    result = _no_existing_notes_lost(_ctx(before, after))
    assert result.passed


def test_no_existing_notes_lost_fail():
    before = _state(patterns=[{"id": 1, "notes": [{"key": 60}, {"key": 62}, {"key": 64}]}])
    after = _state(patterns=[{"id": 1, "notes": [{"key": 60}]}])
    result = _no_existing_notes_lost(_ctx(before, after))
    assert not result.passed
    assert "lost notes" in result.detail


# ------------------------------------------------------ expect_new_pattern #


def test_expect_new_pattern_pass():
    inv = make_expect_new_pattern_invariant()
    before = _state(patterns=[{"id": 1, "notes": []}])
    after = _state(patterns=[{"id": 1, "notes": []}, {"id": 2, "notes": []}])
    result = inv.predicate(_ctx(before, after))
    assert result.passed
    assert "[2]" in result.detail


def test_expect_new_pattern_fail():
    inv = make_expect_new_pattern_invariant()
    before = _state(patterns=[{"id": 1, "notes": []}])
    after = _state(patterns=[{"id": 1, "notes": []}])
    result = inv.predicate(_ctx(before, after))
    assert not result.passed


# ------------------------------------------------------ expect_new_channel #


def test_expect_new_channel_pass():
    inv = make_expect_new_channel_invariant()
    before = _state(channels=[{"iid": 0, "name": "Sampler"}])
    after = _state(channels=[{"iid": 0, "name": "Sampler"}, {"iid": 1, "name": "NewBass"}])
    result = inv.predicate(_ctx(before, after))
    assert result.passed


def test_expect_new_channel_fail():
    inv = make_expect_new_channel_invariant()
    before = _state(channels=[{"iid": 0}])
    after = _state(channels=[{"iid": 0}])
    result = inv.predicate(_ctx(before, after))
    assert not result.passed


# ------------------------------------------------------------ build_invariants #


def test_build_invariants_default():
    invs = build_invariants(min_notes_added=4)
    names = {i.name for i in invs}
    # Always-on base + the dynamic notes_added invariant.
    assert "no_existing_notes_lost" in names
    assert "flpdiff_parses_clean" in names
    assert "notes_added_>=4" in names
    assert "new_pattern_created" not in names
    assert "new_channel_created" not in names


# ----------------------------------------------------------- controllers_added #


def test_controllers_added_pass():
    inv = make_controllers_added_invariant(min_added=4)
    before = _state(patterns=[{"id": 1, "notes": [], "controllers": []}])
    after = _state(
        patterns=[
            {"id": 1, "notes": [], "controllers": [{"position": p} for p in [0, 96, 192, 288]]}
        ]
    )
    result = inv.predicate(_ctx(before, after))
    assert result.passed
    assert "4 controllers added" in result.detail


def test_controllers_added_fail_too_few():
    inv = make_controllers_added_invariant(min_added=4)
    before = _state(patterns=[{"id": 1, "notes": [], "controllers": []}])
    after = _state(
        patterns=[{"id": 1, "notes": [], "controllers": [{"position": 0}, {"position": 96}]}]
    )
    result = inv.predicate(_ctx(before, after))
    assert not result.passed
    assert "only 2 controllers added" in result.detail


def test_controllers_added_handles_missing_field():
    """Patterns may omit the controllers key entirely (= 0)."""
    inv = make_controllers_added_invariant(min_added=1)
    before = _state(patterns=[{"id": 1, "notes": []}])  # no controllers key
    after = _state(patterns=[{"id": 1, "notes": [], "controllers": [{"position": 0}]}])
    result = inv.predicate(_ctx(before, after))
    assert result.passed


def test_build_invariants_includes_controllers_when_requested():
    invs = build_invariants(min_notes_added=0, min_controllers_added=4)
    names = {i.name for i in invs}
    assert "controllers_added_>=4" in names
    # When min_controllers_added=0, no invariant added (default skip).
    invs2 = build_invariants(min_notes_added=4)
    names2 = {i.name for i in invs2}
    assert "controllers_added_>=0" not in names2
    assert not any(n.startswith("controllers_added") for n in names2)


def test_build_invariants_with_optional():
    invs = build_invariants(min_notes_added=2, expect_new_pattern=True, expect_new_channel=True)
    names = {i.name for i in invs}
    assert "new_pattern_created" in names
    assert "new_channel_created" in names


# --------------------------------------------------------- plugin_param helpers #


def _utf16le_nul(s: str) -> bytes:
    return s.encode("utf-16le") + b"\x00\x00"


def _varint(n: int) -> bytes:
    """7-bit LE varint encoding, matching FL's opcode payload prefix."""
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _synthetic_flp(plugin_name: str, blob: bytes) -> bytes:
    """Build minimal bytes containing a UTF-16LE plugin name followed
    by a `0xD5 + varint length + blob` event. Enough for the
    invariant's name-search + varint-walk + size-match path."""
    return _utf16le_nul(plugin_name) + b"\xd5" + _varint(len(blob)) + blob


def _eq2_blob_with_main_level(level_normalized: float) -> bytes:
    """Synthesize a 354-byte EQ 2 blob with main level (0x90 u16 LE)
    set to round(level * 0xFFFF)."""
    blob = bytearray(354)
    raw = round(level_normalized * 0xFFFF)
    struct.pack_into("<H", blob, 0x90, raw)
    return bytes(blob)


def test_find_plugin_blob_matches_size():
    flp = _synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.5))
    found = _find_plugin_blob(flp, "Fruity Parametric EQ 2", 354)
    assert found is not None
    assert len(found) == 354


def test_find_plugin_blob_size_filter_rejects_wrong_size():
    flp = _synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.5))
    # blob is 354; ask for 169 (Limiter size) — should not match
    assert _find_plugin_blob(flp, "Fruity Parametric EQ 2", 169) is None


def test_find_plugin_blob_missing_name():
    flp = _synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.5))
    assert _find_plugin_blob(flp, "Fruity Limiter", 169) is None


def test_decode_param_normalized_u16():
    blob = _eq2_blob_with_main_level(0.8)
    exp = PluginParamExpectation(
        plugin_name="x",
        blob_size=354,
        offset=0x90,
        field_type="u16",
        min_normalized=0,
        max_normalized=1,
    )
    val = _decode_param_normalized(blob, exp)
    assert val is not None
    assert abs(val - 0.8) < 1e-3


def test_decode_param_normalized_i32_bipolar():
    blob = bytearray(66)
    # Reeverb 2 stereo separation: scale=64, encode 0.5 -> signed 0
    struct.pack_into("<i", blob, 0x28, 0)
    exp = PluginParamExpectation(
        plugin_name="x",
        blob_size=66,
        offset=0x28,
        field_type="i32_bipolar",
        scale=64,
        min_normalized=0,
        max_normalized=1,
    )
    assert abs(_decode_param_normalized(bytes(blob), exp) - 0.5) < 1e-6
    # Encode 1.0 -> signed +64
    struct.pack_into("<i", blob, 0x28, 64)
    assert abs(_decode_param_normalized(bytes(blob), exp) - 1.0) < 1e-6


def _ctx_with_files(before_path: Path, after_path: Path) -> InvariantContext:
    return InvariantContext(
        before=_state(),
        after=_state(),
        before_path=before_path,
        after_path=after_path,
    )


def test_plugin_param_changed_invariant_pass(tmp_path: Path):
    before = tmp_path / "before.flp"
    after = tmp_path / "after.flp"
    before.write_bytes(_synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.0)))
    after.write_bytes(_synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.8)))
    inv = make_plugin_param_changed_invariant(
        PluginParamExpectation(
            plugin_name="Fruity Parametric EQ 2",
            blob_size=354,
            offset=0x90,
            field_type="u16",
            min_normalized=0.7,
            max_normalized=0.9,
        )
    )
    result = inv.predicate(_ctx_with_files(before, after))
    assert result.passed
    assert (
        "0.0000 -> 0.8" in result.detail
        or "-> 0.7999" in result.detail
        or "-> 0.8000" in result.detail
    )


def test_plugin_param_changed_invariant_unchanged_fails(tmp_path: Path):
    before = tmp_path / "before.flp"
    after = tmp_path / "after.flp"
    blob = _eq2_blob_with_main_level(0.5)
    before.write_bytes(_synthetic_flp("Fruity Parametric EQ 2", blob))
    after.write_bytes(_synthetic_flp("Fruity Parametric EQ 2", blob))
    inv = make_plugin_param_changed_invariant(
        PluginParamExpectation(
            plugin_name="Fruity Parametric EQ 2",
            blob_size=354,
            offset=0x90,
            field_type="u16",
            min_normalized=0.7,
            max_normalized=0.9,
        )
    )
    result = inv.predicate(_ctx_with_files(before, after))
    assert not result.passed
    assert "unchanged" in result.detail


def test_plugin_param_changed_invariant_out_of_range_fails(tmp_path: Path):
    before = tmp_path / "before.flp"
    after = tmp_path / "after.flp"
    before.write_bytes(_synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.0)))
    # Agent set value to 0.3 — in [0.7, 0.9] target was missed
    after.write_bytes(_synthetic_flp("Fruity Parametric EQ 2", _eq2_blob_with_main_level(0.3)))
    inv = make_plugin_param_changed_invariant(
        PluginParamExpectation(
            plugin_name="Fruity Parametric EQ 2",
            blob_size=354,
            offset=0x90,
            field_type="u16",
            min_normalized=0.7,
            max_normalized=0.9,
        )
    )
    result = inv.predicate(_ctx_with_files(before, after))
    assert not result.passed
    assert "outside expected" in result.detail


def test_build_invariants_includes_plugin_param():
    invs = build_invariants(
        min_notes_added=4,
        expect_plugin_params=[
            PluginParamExpectation(
                plugin_name="Fruity Parametric EQ 2",
                blob_size=354,
                offset=0x90,
                field_type="u16",
                min_normalized=0.7,
                max_normalized=0.9,
            )
        ],
    )
    names = [i.name for i in invs]
    assert any("plugin_param_changed:Fruity Parametric EQ 2@0x90" in n for n in names)
