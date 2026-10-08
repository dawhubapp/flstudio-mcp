"""Tests for flstudio_mcp.music.theory."""

from __future__ import annotations

import pytest

from flstudio_mcp.music.theory import MIDDLE_C_KEY, Key, key_name, key_to_hz, parse_key


@pytest.mark.parametrize(
    ("text", "tonic", "mode"),
    [
        ("C", 0, "major"),
        ("Am", 9, "minor"),
        ("F#m", 6, "minor"),
        ("Bb minor", 10, "minor"),
        ("D dorian", 2, "dorian"),
        ("E major", 4, "major"),
        ("Ebmaj", 3, "major"),
        ("c# phrygian", 1, "phrygian"),
    ],
)
def test_parse_key(text: str, tonic: int, mode: str) -> None:
    assert parse_key(text) == Key(tonic=tonic, mode=mode)


@pytest.mark.parametrize("text", ["", "H", "C blues", "123"])
def test_parse_key_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_key(text)


def test_pitch_classes_a_minor() -> None:
    assert parse_key("Am").pitch_classes() == frozenset({9, 11, 0, 2, 4, 5, 7})


def test_key_name_uses_fl_labels() -> None:
    assert MIDDLE_C_KEY == 60
    assert key_name(60) == "C5"
    assert key_name(45) == "A3"
    assert key_name(61) == "C#5"


def test_key_to_hz() -> None:
    assert key_to_hz(69) == pytest.approx(440.0)
    assert key_to_hz(57) == pytest.approx(220.0)
    assert key_to_hz(60) == pytest.approx(261.6256, rel=1e-4)
