"""Tests for the BeatView half of flstudio_mcp.beat_check."""

from __future__ import annotations

import pytest

from flstudio_mcp import beat_check
from flstudio_mcp.beat_check import build_view, infer_role, section_energy

from .beat_fixtures import BAR, PPQ, good_house, make_describe, note


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("Kick", "kick"),
        ("808 Kick", "kick"),
        ("Sub 808", "808"),
        ("808 · Dark Glide", "808"),
        ("Chords · Warm Rhodes", "chords"),
        ("Stab", "chords"),
        ("Hi-Hat", "hat"),
        ("HH open", "hat"),
        ("Shaker", "perc"),
        ("Bassline Synth", "bass"),
        ("Synth Lead", "lead"),
        ("Clap", "clap"),
        ("Snare", "snare"),
        ("Sampler", None),
        ("", None),
        (None, None),
    ],
)
def test_infer_role(name: str | None, role: str | None) -> None:
    assert infer_role(name) == role


def test_section_energy_by_name() -> None:
    assert section_energy("Intro") == 0.3
    assert section_energy("Main Drop") == 1.0
    assert section_energy("Verse+Hook") == 0.9  # max of matched words
    assert section_energy("Pattern 3") is None


def test_build_view_good_house() -> None:
    view = build_view(good_house())
    assert view.ppq == PPQ and view.tempo == 124.0 and view.beats_per_bar == 4
    assert [s.name for s in view.sections] == ["Intro", "Build", "Drop"]
    assert [s.start // BAR for s in view.sections] == [0, 8, 16]
    assert view.total_bars == 32
    assert view.roles[3] == "bass" and view.roles[4] == "chords"
    assert view.pitch_follows_key[3] is True  # plugin synth
    assert view.pitch_follows_key[0] is False  # sampler
    assert view.seconds(BAR) == pytest.approx(4 * 60 / 124)


def test_roles_override_inference() -> None:
    view = build_view(good_house(), roles={4: "lead"})
    assert view.roles[4] == "lead"
    assert view.roles[3] == "bass"


def test_overlapping_clips_merge_into_one_section() -> None:
    d = make_describe(
        channels={0: "Kick", 1: "Bass"},
        patterns={
            1: ("Drums", [note(i * PPQ, 60, 0) for i in range(32)]),
            2: ("Bass", [note(i * PPQ, 36, 1) for i in range(32)]),
        },
        clips=[(1, 0, 8, 1), (2, 0, 8, 2), (1, 8, 8, 1)],
    )
    view = build_view(d)
    assert [(s.name, s.start // BAR, s.length // BAR) for s in view.sections] == [
        ("Drums+Bass", 0, 8),
        ("Drums", 8, 8),
    ]
    channels = {n.channel_iid for n in view.notes_in(view.sections[0])}
    assert channels == {0, 1}


def test_notes_in_clips_to_clip_length() -> None:
    d = make_describe(
        channels={0: "Kick"},
        patterns={1: ("Intro", [note(i * BAR, 60, 0) for i in range(8)])},
        clips=[(1, 4, 2)],  # 2-bar clip of an 8-bar pattern
    )
    view = build_view(d)
    assert [n.position for n in view.notes_in(view.sections[0])] == [0, BAR]


def test_short_pattern_loops_only_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    d = make_describe(
        channels={0: "Kick"},
        patterns={1: ("Intro", [note(0, 60, 0)])},
        clips=[(1, 0, 4)],
    )
    view = build_view(d)
    assert [n.position for n in view.notes_in(view.sections[0])] == [0]
    monkeypatch.setattr(beat_check, "PATTERN_CLIPS_LOOP", True)
    assert [n.position for n in view.notes_in(view.sections[0])] == [0, BAR, 2 * BAR, 3 * BAR]


def test_muted_clips_and_channel_clips_are_ignored() -> None:
    d = make_describe(
        channels={0: "Kick"}, patterns={1: ("A", [note(0, 60, 0)])}, clips=[(1, 0, 1)]
    )
    d["arrangements"][0]["tracks"][0]["items"].append(
        {"position": BAR, "length": BAR, "pattern_iid": 1, "channel_iid": None, "muted": True}
    )
    d["arrangements"][0]["tracks"][0]["items"].append(
        {"position": BAR, "length": BAR, "pattern_iid": None, "channel_iid": 0, "muted": False}
    )
    assert len(build_view(d).sections) == 1


def test_empty_project_has_no_sections() -> None:
    view = build_view({"_type": "FLPProject", "metadata": {}, "channels": [], "patterns": []})
    assert view.sections == [] and view.total_bars == 0 and view.ppq == 96
