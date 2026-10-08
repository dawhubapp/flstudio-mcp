"""Brief cases for the beat-quality loop (F11.1.4)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from flstudio_mcp.beat_check import GENRES

from .full_song import CORPUS_DIR

BRIEFS_PATH = Path(__file__).resolve().parents[1] / "briefs.toml"
BASE_FLP = CORPUS_DIR / "re_base" / "fl25" / "base_empty.flp"
# Baseline uses today's prompt; F11.3.2 switches brief runs to the shipped make_beat prompt.
PROMPT_PATH = Path(__file__).resolve().parents[1] / "harness" / "prompts" / "full_song.md"
# Comma-separated prompt addenda (prompts/addendum_<name>.md) for A/B runs, e.g. "blueprint".
ADDENDUM_ENV = "FLSTUDIO_BRIEF_ADDENDUM"
SETS = ("tuning", "held_out")

AGENT_MODEL = "claude-opus-5-5"
AGENT_EFFORT = "high"
MAX_ITERATIONS = 40
MAX_OUTPUT_TOKENS = 16000
# Counts every prompt token incl. cache reads (harness cap semantics); ~$0.6 with caching.
MAX_INPUT_TOKENS = 2_000_000
USER_PROMPT_TEMPLATE = (
    "Make a 32-bar beat from scratch in this empty FL Studio project.\n"
    "Brief: {brief}\n\n"
    "Project file: {path}"
)


@dataclass(frozen=True)
class Brief:
    id: str
    genre: str
    set: str
    text: str


def load_briefs(path: Path = BRIEFS_PATH) -> list[Brief]:
    """Load and validate the brief set."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    briefs: list[Brief] = []
    for raw in data.get("brief", []):
        missing = {"id", "genre", "set", "text"} - set(raw)
        if missing:
            raise ValueError(f"brief {raw.get('id', '?')!r} missing {sorted(missing)}")
        brief = Brief(id=raw["id"], genre=raw["genre"], set=raw["set"], text=raw["text"])
        if brief.genre not in GENRES:
            raise ValueError(f"brief {brief.id!r}: genre must be one of {list(GENRES)}")
        if brief.set not in SETS:
            raise ValueError(f"brief {brief.id!r}: set must be one of {list(SETS)}")
        briefs.append(brief)
    ids = [b.id for b in briefs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate brief ids")
    return briefs


def system_prompt(base: Path, addenda: str) -> tuple[str, str]:
    """Base prompt plus ``addendum_<name>.md`` files next to it; returns (text, label)."""
    parts = [base.read_text(encoding="utf-8")]
    label = base.name
    for name in filter(None, (n.strip() for n in addenda.split(","))):
        path = base.parent / f"addendum_{name}.md"
        if not path.is_file():
            raise ValueError(f"unknown prompt addendum {name!r} ({path.name} not found)")
        parts.append(path.read_text(encoding="utf-8"))
        label += f"+{path.name}"
    return "\n\n".join(p.strip("\n") for p in parts), label
