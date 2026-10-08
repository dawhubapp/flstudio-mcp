"""Tests for check_beat (Tier 0 symbolic checks)."""

from __future__ import annotations

import json

import pytest

from flstudio_mcp.beat_check import check_beat

from .beat_fixtures import BAR, PPQ, good_house, make_describe, note


def _msgs(result, severity: str) -> list[str]:
    return [i.message for i in result.issues if i.severity == severity]


def test_good_house_passes_cleanly() -> None:
    result = check_beat(good_house(), "house", key="Am")
    assert result.ok, result.to_dict()
    assert [i for i in result.issues if i.severity != "info"] == []
    assert result.metrics["total_bars"] == 32
    assert [s["name"] for s in result.metrics["sections"]] == ["Intro", "Build", "Drop"]
    assert result.metrics["sections"][2]["start_bar"] == 17
    assert result.metrics["roles"]["bass"]["key_range"] == [33, 40]
    json.dumps(result.to_dict(), allow_nan=False)


def test_unknown_genre_and_key_raise() -> None:
    with pytest.raises(ValueError):
        check_beat(good_house(), "polka")
    with pytest.raises(ValueError):
        check_beat(good_house(), "house", key="C blues")


def test_nothing_arranged_is_an_error() -> None:
    d = make_describe(channels={0: "Kick"}, patterns={1: ("A", [note(0, 60, 0)])}, clips=[])
    result = check_beat(d, "house")
    assert not result.ok
    assert any("nothing is arranged" in m for m in _msgs(result, "error"))


def test_empty_pattern_clip_is_an_error() -> None:
    d = make_describe(channels={0: "Kick"}, patterns={1: ("Drop", [])}, clips=[(1, 0, 4)])
    assert any("has no notes" in m for m in _msgs(check_beat(d, "house"), "error"))


def test_short_pattern_in_long_clip_reports_silent_bars() -> None:
    d = make_describe(
        channels={0: "Kick"},
        patterns={1: ("Intro", [note(b * PPQ, 60, 0) for b in range(4)])},
        clips=[(1, 0, 4)],
    )
    assert any("3 silent bar(s) out of 4" in m for m in _msgs(check_beat(d, "house"), "warning"))


def test_bass_register_error_on_synth_channel() -> None:
    d = good_house()
    for p in d["patterns"]:
        for n in p["notes"]:
            if n["channel_iid"] == 3:
                n["key"] += 36  # 69..76 — three octaves up
    result = check_beat(d, "house")
    errors = [i for i in result.issues if i.severity == "error" and i.role == "bass"]
    assert errors and "expected 24-55" in errors[0].message


def test_register_not_checked_on_sampler_channel() -> None:
    d = good_house()
    d["channels"][3].update(kind="sampler", plugin=None, sample_path="%FLStudioFactoryData%/b.wav")
    for p in d["patterns"]:
        for n in p["notes"]:
            if n["channel_iid"] == 3:
                n["key"] = 72
    result = check_beat(d, "house")
    assert not [i for i in result.issues if i.role == "bass" and i.severity == "error"]
    assert any("sample-based" in i.message for i in result.issues if i.severity == "info")


def test_flat_hat_velocities_warn() -> None:
    d = good_house()
    for p in d["patterns"]:
        for n in p["notes"]:
            if n["channel_iid"] == 2:
                n["velocity"] = 100
    assert any("hat velocities are flat" in m for m in _msgs(check_beat(d, "house"), "warning"))


def test_single_note_chords_are_an_error() -> None:
    d = good_house()
    drop = d["patterns"][2]
    seen: set[int] = set()  # keep one chords note per onset
    kept = []
    for n in drop["notes"]:
        if n["channel_iid"] == 4:
            if n["position"] in seen:
                continue
            seen.add(n["position"])
        kept.append(n)
    drop["notes"] = kept
    assert any("3+ note voicings" in m for m in _msgs(check_beat(d, "house"), "error"))


def test_out_of_key_notes_warn_only_when_key_given() -> None:
    d = good_house()
    for n in d["patterns"][2]["notes"]:
        if n["channel_iid"] == 4:
            n["key"] += 1  # semitone off everywhere
    assert not any("outside" in m for m in _msgs(check_beat(d, "house"), "warning"))
    assert any("outside A minor" in m for m in _msgs(check_beat(d, "house", key="Am"), "warning"))


def test_house_drop_needs_four_on_the_floor() -> None:
    d = good_house()
    d["patterns"][2]["notes"] = [
        n for n in d["patterns"][2]["notes"] if not (n["channel_iid"] == 0 and n["position"] % BAR)
    ]
    result = check_beat(d, "house")
    assert any("kick on 16/64 beats" in m for m in _msgs(result, "error"))


def _trap(backbeat_on_three: bool) -> dict:
    hook_notes = []
    for b in range(8):
        hook_notes.append(note(b * BAR, 60, 0, velocity=110))
        hook_notes.append(note(b * BAR + (2 if backbeat_on_three else 1) * PPQ, 60, 1))
        hook_notes += [
            note(b * BAR + i * 24, 60, 2, length=12, velocity=60 + 3 * i) for i in range(16)
        ]
        hook_notes.append(note(b * BAR, 36, 3, length=BAR, velocity=100))
    return make_describe(
        channels={0: "Kick", 1: "Snare", 2: "Hat", 3: "808"},
        patterns={1: ("Hook", hook_notes)},
        clips=[(1, 0, 8)],
        tempo=140.0,
    )


def test_trap_hook_backbeat_on_three() -> None:
    assert not any("beat 3" in m for m in _msgs(check_beat(_trap(True), "trap"), "error"))
    assert any("beat 3 in 0/8 bars" in m for m in _msgs(check_beat(_trap(False), "trap"), "error"))


def test_no_high_energy_section_is_info() -> None:
    d = make_describe(
        channels={0: "Kick", 1: "Hat", 2: "Bass"},
        patterns={1: ("Pattern 1", [note(i * PPQ, 60, 0) for i in range(16)])},
        clips=[(1, 0, 4)],
    )
    assert any("no high-energy section" in m for m in _msgs(check_beat(d, "house"), "info"))


def test_identical_adjacent_sections_warn() -> None:
    d = good_house()
    d["arrangements"][0]["tracks"][0]["items"].append(
        {
            "position": 32 * BAR,
            "length": 16 * BAR,
            "pattern_iid": 3,
            "channel_iid": None,
            "muted": False,
        }
    )
    assert any("sound the same" in m for m in _msgs(check_beat(d, "house"), "warning"))


def test_missing_core_role_is_an_error() -> None:
    d = good_house()
    for p in d["patterns"]:
        p["notes"] = [n for n in p["notes"] if n["channel_iid"] != 3]
    assert any("no bass or 808 notes" in m for m in _msgs(check_beat(d, "house"), "error"))


def test_unknown_role_channel_is_info() -> None:
    d = good_house()
    d["channels"].append({"iid": 9, "name": "Sampler", "kind": "sampler", "plugin": None})
    d["patterns"][2]["notes"].append(note(0, 60, 9))
    assert any("no recognizable role" in m for m in _msgs(check_beat(d, "house"), "info"))
