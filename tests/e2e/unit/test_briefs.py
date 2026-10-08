"""Unit tests for the brief loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..cases.briefs import BASE_FLP, load_briefs


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
