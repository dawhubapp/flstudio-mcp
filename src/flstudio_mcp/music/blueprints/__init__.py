"""Genre blueprints: data-only arrangement rules, one ``<genre>.toml`` per genre."""

from __future__ import annotations

import tomllib
from importlib.resources import files
from typing import Any


class BlueprintError(ValueError):
    """Raised for a genre without a blueprint."""


def available_genres() -> list[str]:
    """Genres that ship a blueprint, sorted."""
    return sorted(
        p.name.removesuffix(".toml") for p in files(__name__).iterdir() if p.name.endswith(".toml")
    )


def load_blueprint(genre: str) -> dict[str, Any]:
    """Load ``<genre>.toml``; forms come back as ``[(section, bars), ...]``."""
    genres = available_genres()
    if genre not in genres:
        raise BlueprintError(f"no blueprint for {genre!r}; available: {', '.join(genres)}")
    data = tomllib.loads((files(__name__) / f"{genre}.toml").read_text(encoding="utf-8"))
    data["forms"] = {
        name: [(str(section), int(bars)) for section, bars in form]
        for name, form in data["forms"].items()
    }
    return data
