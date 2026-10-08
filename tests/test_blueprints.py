"""Tests for the genre blueprints (music/blueprints/<genre>.toml)."""

from __future__ import annotations

import pytest

from flstudio_mcp.beat_check import DEFAULT_SECTION_ENERGY, ROLES
from flstudio_mcp.music.blueprints import BlueprintError, available_genres, load_blueprint


def test_house_is_available() -> None:
    assert "house" in available_genres()


def test_unknown_genre_lists_the_available_ones() -> None:
    with pytest.raises(BlueprintError, match="house"):
        load_blueprint("polka")


def test_forms_are_whole_phrases_of_known_sections() -> None:
    bp = load_blueprint("house")
    phrase = bp["phrase_bars"]
    for form in bp["forms"].values():
        assert form, "empty form"
        for section, bars in form:
            assert section in bp["sections"]
            assert bars % phrase == 0
            assert bars in bp["sections"][section]["bars"]
    assert sum(bars for _, bars in bp["forms"]["beat_32"]) == 32


def test_parts_use_known_roles_and_layers() -> None:
    bp = load_blueprint("house")
    for section in bp["sections"].values():
        assert section["parts"]
        for part, share in section["parts"].items():
            role, layer = part.split(".")
            assert role in ROLES
            assert layer in bp["layers"][role]
            assert 0 < share <= 1


def test_section_energy_matches_check_beat() -> None:
    bp = load_blueprint("house")
    for name, section in bp["sections"].items():
        assert section["energy"] == DEFAULT_SECTION_ENERGY[name]
        assert all(bars % bp["phrase_bars"] == 0 for bars in section["bars"])


def test_house_encodes_the_measured_arrangement_rules() -> None:
    s = load_blueprint("house")["sections"]
    parts = {name: sec["parts"] for name, sec in s.items()}
    # Breaks drop the kick; drops keep it.
    assert parts["break"]["kick.main"] < 0.5 <= parts["drop"]["kick.main"]
    # Builds are a snare roll over the whole section.
    assert parts["build"]["snare.build"] >= 0.9
    # The second bass layer is a drop-only lift.
    others = ("intro", "break", "build", "outro")
    assert parts["drop"]["bass.layer"] > max(parts[o].get("bass.layer", 0) for o in others)
    # The ride lives outside the drop.
    assert parts["drop"].get("hat.ride", 0) < parts["break"]["hat.ride"]


def test_transitions_are_probabilities() -> None:
    t = load_blueprint("house")["transitions"]
    assert 0.5 < t["phrase_change"] <= 1
    assert 0.5 < t["phrase_end_any"] <= 1
    for table in ("phrase_end", "pre_drop_silent"):
        assert t[table]
        assert all(0 < p <= 1 for p in t[table].values())
