"""Unit tests for the reorganize invariant predicates.

Feeds canned ``FLPState`` dicts to each predicate and asserts the
expected pass/fail. Runs in normal CI without an API key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..harness.invariants import (
    PALETTE,
    REORGANIZE_INVARIANTS,
    Invariant,
    InvariantContext,
    InvariantResult,
    _clips_preserved,
    _flpdiff_clean,
    _notes_preserved,
    _palette_colors,
    _routing_distinct_non_master,
    _semantic_names,
    run_invariants,
)
from ..harness.state_capture import FLPState


def _state(
    *,
    channels: list[dict[str, Any]] | None = None,
    mixer: list[dict[str, Any]] | None = None,
    patterns: list[dict[str, Any]] | None = None,
    tracks: list[dict[str, Any]] | None = None,
    clips: list[dict[str, Any]] | None = None,
    arrangements: list[dict[str, Any]] | None = None,
) -> FLPState:
    return FLPState(
        path=Path("/tmp/fake.flp"),
        describe={},
        channels=channels or [],
        mixer=mixer or [],
        patterns=patterns or [],
        tracks=tracks or [],
        clips=clips or [],
        arrangements=arrangements or [],
    )


def _ctx(before: FLPState, after: FLPState) -> InvariantContext:
    return InvariantContext(
        before=before,
        after=after,
        before_path=Path("/tmp/before.flp"),
        after_path=Path("/tmp/after.flp"),
    )


# ---------------------------------------------------------------- semantic_names #


def test_semantic_names_pass_when_all_renamed() -> None:
    state = _state(
        channels=[{"iid": 1, "name": "Kick"}, {"iid": 2, "name": "Bass Sub"}],
        mixer=[{"name": "Drums Bus"}],
        patterns=[{"iid": 1, "name": "Verse Loop"}],
        tracks=[{"name": "Drums"}],
    )
    res = _semantic_names(_ctx(state, state))
    assert res.passed is True


def test_semantic_names_fail_on_default_name() -> None:
    state = _state(channels=[{"iid": 1, "name": "Sample 7"}])
    res = _semantic_names(_ctx(state, state))
    assert res.passed is False
    assert "Sample 7" in res.detail


def test_semantic_names_ignores_unnamed_objects() -> None:
    """An empty/missing name doesn't count as 'still default-named'."""
    state = _state(channels=[{"iid": 1, "name": ""}, {"iid": 2}])
    res = _semantic_names(_ctx(state, state))
    assert res.passed is True


# ---------------------------------------------------------------- routing #


def test_routing_pass_each_channel_distinct_insert() -> None:
    state = _state(
        channels=[
            {"iid": 1, "enabled": True, "routing": 5},
            {"iid": 2, "enabled": True, "routing": 6},
        ]
    )
    res = _routing_distinct_non_master(_ctx(state, state))
    assert res.passed is True


def test_routing_fail_when_on_master() -> None:
    state = _state(channels=[{"iid": 1, "enabled": True, "routing": 0}])
    res = _routing_distinct_non_master(_ctx(state, state))
    assert res.passed is False
    assert "Master" in res.detail


def test_routing_fail_when_two_channels_share_insert() -> None:
    state = _state(
        channels=[
            {"iid": 1, "enabled": True, "routing": 5},
            {"iid": 2, "enabled": True, "routing": 5},
        ]
    )
    res = _routing_distinct_non_master(_ctx(state, state))
    assert res.passed is False
    assert "multiple" in res.detail


def test_routing_fail_when_no_routing_field() -> None:
    state = _state(channels=[{"iid": 1, "enabled": True}])
    res = _routing_distinct_non_master(_ctx(state, state))
    assert res.passed is False


def test_routing_dict_shape_accepted() -> None:
    state = _state(
        channels=[
            {"iid": 1, "enabled": True, "routing": {"target_insert": 5}},
            {"iid": 2, "enabled": True, "routing": {"target_insert": 6}},
        ]
    )
    res = _routing_distinct_non_master(_ctx(state, state))
    assert res.passed is True


def test_routing_skips_disabled_channels() -> None:
    state = _state(
        channels=[
            {"iid": 1, "enabled": False, "routing": 0},
            {"iid": 2, "enabled": True, "routing": 5},
        ]
    )
    res = _routing_distinct_non_master(_ctx(state, state))
    assert res.passed is True


# ---------------------------------------------------------------- palette #


def test_palette_pass_when_all_colors_in_set() -> None:
    state = _state(
        channels=[{"color": PALETTE["bass"]}],
        mixer=[{"color": PALETTE["lead"]}],
    )
    res = _palette_colors(_ctx(state, state))
    assert res.passed is True


def test_palette_fail_on_off_palette_color() -> None:
    state = _state(channels=[{"color": 0xFF000000}])  # pure black, not in palette
    res = _palette_colors(_ctx(state, state))
    assert res.passed is False
    assert "0xFF000000" in res.detail


def test_palette_accepts_dict_color_shape() -> None:
    """RGBA dict {a, r, g, b} matches palette int."""
    # 0xFF22C55E = (a=FF, r=22, g=C5, b=5E)
    state = _state(channels=[{"color": {"a": 0xFF, "r": 0x22, "g": 0xC5, "b": 0x5E}}])
    res = _palette_colors(_ctx(state, state))
    assert res.passed is True


def test_palette_ignores_zero_color() -> None:
    """Unset colors (0) are not flagged."""
    state = _state(channels=[{"color": 0}])
    res = _palette_colors(_ctx(state, state))
    assert res.passed is True


# ---------------------------------------------------------------- clips #


def test_clips_pass_when_count_unchanged() -> None:
    before = _state(clips=[{"position": 0}, {"position": 96}])
    after = _state(clips=[{"position": 96}, {"position": 0}])  # reordered, same count
    res = _clips_preserved(_ctx(before, after))
    assert res.passed is True


def test_clips_fail_when_clip_added() -> None:
    before = _state(clips=[{"position": 0}])
    after = _state(clips=[{"position": 0}, {"position": 96}])
    res = _clips_preserved(_ctx(before, after))
    assert res.passed is False


# ---------------------------------------------------------------- notes #


def test_notes_pass_when_per_pattern_count_unchanged() -> None:
    before = _state(patterns=[{"iid": 1, "notes": [{"key": 60}, {"key": 62}]}])
    after = _state(patterns=[{"iid": 1, "notes": [{"key": 60}, {"key": 62}]}])
    res = _notes_preserved(_ctx(before, after))
    assert res.passed is True


def test_notes_fail_when_pattern_lost_a_note() -> None:
    before = _state(patterns=[{"iid": 1, "notes": [{"key": 60}, {"key": 62}]}])
    after = _state(patterns=[{"iid": 1, "notes": [{"key": 60}]}])
    res = _notes_preserved(_ctx(before, after))
    assert res.passed is False


def test_notes_accepts_int_count_shape() -> None:
    before = _state(patterns=[{"iid": 1, "notes": 4}])
    after = _state(patterns=[{"iid": 1, "notes": 4}])
    res = _notes_preserved(_ctx(before, after))
    assert res.passed is True


# ---------------------------------------------------------------- flpdiff_clean #


def test_flpdiff_clean_skips_when_binary_missing() -> None:
    state = _state()
    ctx = InvariantContext(
        before=state,
        after=state,
        before_path=Path("/tmp/a.flp"),
        after_path=Path("/tmp/b.flp"),
        flpdiff_cmd=["/nonexistent/flpdiff_xyz"],
    )
    res = _flpdiff_clean(ctx)
    assert res.passed is False
    assert "not found" in res.detail


# ---------------------------------------------------------------- run_invariants #


def test_run_invariants_aggregates_all() -> None:
    state = _state(
        channels=[
            {
                "iid": 1,
                "name": "Kick",
                "enabled": True,
                "routing": 5,
                "color": PALETTE["drums_hard"],
            }
        ],
        clips=[],
        patterns=[],
    )
    # Drop flpdiff_clean for this aggregation test (no on-disk FLPs).
    invariants = [inv for inv in REORGANIZE_INVARIANTS if inv.name != "flpdiff_clean"]
    report = run_invariants(invariants, _ctx(state, state))
    assert report.passed_hard is True
    assert len(report.results) == len(invariants)


def test_predicate_exception_surfaced_as_soft_fail() -> None:
    def boom(_ctx: InvariantContext) -> InvariantResult:
        raise RuntimeError("kaboom")

    invariants = [Invariant("boom", "hard", boom)]
    state = _state()
    report = run_invariants(invariants, _ctx(state, state))
    assert report.passed_hard is False
    [(_inv, res)] = report.results
    assert "kaboom" in res.detail


def test_invariant_report_summary_format() -> None:
    state = _state(channels=[{"name": "Sample 1"}])  # default-named — fail
    report = run_invariants([REORGANIZE_INVARIANTS[0]], _ctx(state, state))
    summary = report.summary()
    assert "FAIL" in summary
    assert "semantic_names" in summary
