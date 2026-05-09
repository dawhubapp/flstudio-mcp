"""Unit tests for the full-song-flow invariant predicates.

Feeds canned ``FLPState`` dicts to ``invariants_song.build_invariants``
and asserts the expected pass/fail. Runs in normal CI without an API
key. The ``flpdiff_parses_clean`` invariant calls the CLI; tests skip
that one (covered by the e2e tests' actual round-trip).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..harness.invariants import InvariantContext
from ..harness.invariants_song import (
    _no_existing_notes_lost,
    build_invariants,
    make_expect_new_channel_invariant,
    make_expect_new_pattern_invariant,
    make_notes_added_invariant,
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


def test_build_invariants_with_optional():
    invs = build_invariants(min_notes_added=2, expect_new_pattern=True, expect_new_channel=True)
    names = {i.name for i in invs}
    assert "new_pattern_created" in names
    assert "new_channel_created" in names
