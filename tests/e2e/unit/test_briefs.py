"""Unit tests for the brief loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..cases.briefs import BASE_FLP, PROMPT_PATH, load_briefs, system_prompt


def test_shipped_briefs() -> None:
    briefs = load_briefs()
    assert [b.id for b in briefs] == [
        "house_deep", "house_drive", "trap_dark", "trap_bounce", "house_uplift", "trap_cloudy",
    ]  # fmt: skip
    assert sum(b.set == "tuning" for b in briefs) == 4
    assert {b.genre for b in briefs} == {"house", "trap"}
    assert BASE_FLP.name == "base_empty.flp"


@pytest.mark.parametrize(
    "body",
    [
        '[[brief]]\nid="a"\ngenre="polka"\nset="tuning"\ntext="x"\n',
        '[[brief]]\nid="a"\ngenre="house"\nset="maybe"\ntext="x"\n',
        '[[brief]]\nid="a"\ngenre="house"\nset="tuning"\ntext="x"\n' * 2,
        '[[brief]]\nid="a"\ngenre="house"\nset="tuning"\n',
    ],
)
def test_invalid_brief_files(tmp_path: Path, body: str) -> None:
    path = tmp_path / "b.toml"
    path.write_text(body)
    with pytest.raises(ValueError):
        load_briefs(path)


def test_system_prompt_without_addenda_is_the_base_prompt(tmp_path: Path) -> None:
    base = tmp_path / "base.md"
    base.write_text("BASE")
    assert system_prompt(base, "") == ("BASE", "base.md")


def test_system_prompt_appends_named_addenda(tmp_path: Path) -> None:
    base = tmp_path / "base.md"
    base.write_text("BASE")
    (tmp_path / "addendum_blueprint.md").write_text("RULES")
    text, label = system_prompt(base, "blueprint")
    assert text == "BASE\n\nRULES"
    assert label == "base.md+addendum_blueprint.md"
    with pytest.raises(ValueError, match="nope"):
        system_prompt(base, "blueprint,nope")


def test_shipped_blueprint_addendum_exists() -> None:
    text, label = system_prompt(PROMPT_PATH, "blueprint")
    assert "get_blueprint" in text and label.endswith("+addendum_blueprint.md")
