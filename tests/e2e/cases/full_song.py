"""Test case definitions for the full-song-edit flow (Flow 2).

Flow 2 exercises the F2.x mutation kinds: notes, controllers,
pattern/channel creation, native plugin params. Each case bundles a
baseline FLP, a user prompt describing the musical addition, and the
minimum required outcome (note count delta, channel/pattern creation,
etc.) checked by ``invariants_song.FULL_SONG_INVARIANTS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parents[4] / "flpdiff" / "tests" / "corpus"


@dataclass
class FullSongCase:
    """One full-song-flow test case."""

    id: str
    input_flp: Path
    user_prompt: str
    min_notes_added: int  # post.total_notes - pre.total_notes >= this
    min_grade: int
    max_iterations: int = 15
    max_input_tokens: int = 200_000
    model: str = "claude-haiku-4-5-20251001"
    description: str = ""
    expect_new_pattern: bool = False
    expect_new_channel: bool = False
    extra_invariants: list = field(default_factory=list)


FULL_SONG_CASES: list[FullSongCase] = [
    FullSongCase(
        id="add_bass_melody_smoke",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        user_prompt=(
            "Add a simple 4-note bassline to the existing pattern in this "
            "project. Use the existing kick channel (the one with a "
            "sample_path). Notes should be on the root (C2 = FL key 36), "
            "spaced one beat apart, length = 1 beat each. Project path: "
        ),
        min_notes_added=4,
        min_grade=3,
        max_iterations=10,
        description=(
            "Synthetic 1-pattern FLP — smoke that the agent can read state, "
            "then call add_pattern_note (or set_pattern_notes) 4+ times to "
            "land bass notes on the existing channel + pattern."
        ),
    ),
]
