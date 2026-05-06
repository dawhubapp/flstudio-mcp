"""Test case definitions for the reorganize-project flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parents[4] / "flpdiff" / "tests" / "corpus"


@dataclass
class ReorganizeCase:
    """One reorganize-flow test case."""

    id: str
    input_flp: Path
    min_grade: int  # judge minimum-pass grade
    max_iterations: int = 30
    max_input_tokens: int = 200_000  # agent loop cumulative-input cap
    model: str = "claude-sonnet-4-6"  # smoke uses sonnet for cost; bigger cases override
    description: str = ""
    extra_invariants: list = field(default_factory=list)


REORGANIZE_CASES: list[ReorganizeCase] = [
    ReorganizeCase(
        id="synthetic_smoke",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        min_grade=3,
        max_iterations=8,
        # Haiku 4.5: low cost + much higher tokens-per-minute rate
        # limit than sonnet, more than enough headroom for a 1-channel
        # synthetic. Real-FLP cases override to opus.
        model="claude-haiku-4-5-20251001",
        description="1-channel synthetic FLP — smoke test for the harness wiring + first real API call.",
    ),
    ReorganizeCase(
        id="bass_sketch_real",
        input_flp=CORPUS_DIR / "local" / "bass_sketch.flp",
        min_grade=4,
        max_iterations=30,
        # Real FLPs balloon `describe` payloads to 60k+ tokens; the
        # default 200k cap fires within 3 iterations. Opus has 1M
        # context — give it 500k so the agent has headroom.
        max_input_tokens=500_000,
        model="claude-opus-4-7",
        description="Real producer FLP (gitignored). Used for the full Ableton-style reorganize verification.",
    ),
]
