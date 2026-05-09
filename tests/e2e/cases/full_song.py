"""Test case definitions for the full-song-edit flow (Flow 2).

Flow 2 exercises the F2.x mutation kinds: notes, controllers,
pattern/channel creation, native plugin params. Each case bundles a
baseline FLP, a user prompt describing the musical addition, and the
minimum required outcome (note count delta, channel/pattern creation,
plugin-param byte-range, etc.) checked by
``invariants_song.build_invariants``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..harness.invariants_song import ChannelLevelExpectation, PluginParamExpectation

CORPUS_DIR = Path(__file__).resolve().parents[4] / "flpdiff" / "tests" / "corpus"


@dataclass
class FullSongCase:
    """One full-song-flow test case."""

    id: str
    input_flp: Path
    user_prompt: str
    min_notes_added: int  # post.total_notes - pre.total_notes >= this
    min_grade: int
    min_controllers_added: int = 0  # post.total_controllers - pre >= this (0 = unchecked)
    max_iterations: int = 15
    max_input_tokens: int = 500_000  # opus has 1M context; give headroom
    # Default agent + judge models per project standard: opus drives,
    # sonnet grades. Cases override per-test if needed.
    model: str = "claude-opus-4-7"
    judge_model: str = "claude-sonnet-4-6"
    description: str = ""
    expect_new_pattern: bool = False
    expect_new_channel: bool = False
    expect_plugin_params: list[PluginParamExpectation] = field(default_factory=list)
    expect_channel_levels: list[ChannelLevelExpectation] = field(default_factory=list)
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
        min_grade=4,
        max_iterations=10,
        description=(
            "Synthetic 1-pattern FLP — smoke that the agent can read state, "
            "then call add_pattern_note (or set_pattern_notes) 4+ times to "
            "land bass notes on the existing channel + pattern."
        ),
    ),
    FullSongCase(
        id="tweak_eq2_and_compose",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_insert.flp",
        user_prompt=(
            "This project has a Sampler channel and a Fruity Parametric "
            "EQ 2 loaded on insert 1, slot 0. Do two things: "
            "(1) create a new pattern named 'Hook' and add a 4-note "
            "melody on the Sampler channel in the C5..C6 range "
            "(FL keys 60..84), quarter-note grid (positions 0, 96, 192, "
            "288 at PPQ=96), velocity 100, length=96. "
            "(2) Boost the EQ 2 main level on insert 1, slot 0 to "
            "approximately 0.8 (in normalized 0..1 units). "
            "Use set_native_plugin_param with scope='mixer_slot', "
            "param={'kind': 'main_level'}, value=0.8. "
            "Project path: "
        ),
        min_notes_added=4,
        min_grade=4,
        max_iterations=20,
        expect_new_pattern=True,
        expect_plugin_params=[
            # EQ 2 main level lives at byte 0x90 of the 354-byte blob;
            # u16 LE = round(v * 0xFFFF). Accept [0.7, 0.95] so the
            # agent has float-precision wiggle room around 0.8.
            PluginParamExpectation(
                plugin_name="Fruity Parametric EQ 2",
                blob_size=354,
                offset=0x90,
                field_type="u16",
                min_normalized=0.7,
                max_normalized=0.95,
            ),
        ],
        description=(
            "Hard case: exercises create_pattern + add_pattern_note + "
            "set_native_plugin_param end-to-end. Verifies the agent "
            "can patch a native FL plugin parameter into an expected "
            "byte range while also composing musical content."
        ),
    ),
    FullSongCase(
        id="mix_balance_pass",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        user_prompt=(
            "This project has 2 channels (Sampler iid=0, Kick iid=1). "
            "Both default to volume 0.78 (raw 10000) and pan center. "
            "Do a quick mix balance: "
            "(1) Lower the Kick volume to 0.5 via set_channel_volume "
            "(iid=1). "
            "(2) Pan the Sampler 30% to the right via set_channel_pan "
            "(iid=0, value=0.3). "
            "Don't add or remove notes. Project path: "
        ),
        min_notes_added=0,
        min_grade=4,
        max_iterations=10,
        expect_channel_levels=[
            ChannelLevelExpectation(
                iid=1,
                field="volume",
                min_normalized=0.45,
                max_normalized=0.55,
            ),
            ChannelLevelExpectation(
                iid=0,
                field="pan",
                min_normalized=0.20,
                max_normalized=0.40,
            ),
        ],
        description=(
            "Exercises set_channel_volume + set_channel_pan (F6.2). "
            "Verifies the agent can do basic mixing without composing."
        ),
    ),
    FullSongCase(
        id="humanize_stiff_pattern",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        user_prompt=(
            "The existing pattern feels robotic. Make it groovier: "
            "(1) humanize the velocities with range 15 (use "
            "humanize_velocities); (2) add 4 transposed copies of the "
            "existing note one octave LOWER (key 51 instead of 63) "
            "spaced one beat apart (positions 0, 96, 192, 288 at "
            "PPQ=96). Use add_pattern_note for each new note on the "
            "kick channel. Project path: "
        ),
        min_notes_added=4,
        min_grade=4,
        max_iterations=15,
        description=(
            "Exercises humanize_velocities + add_pattern_note. Verifies "
            "the agent can mix musical-transformation kinds with "
            "compose-style kinds."
        ),
    ),
    FullSongCase(
        id="add_volume_automation",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        user_prompt=(
            "This project has a kick channel and one pattern with a "
            "single note. Add a 4-keyframe volume swell automation to "
            "that pattern, scoped to the kick channel. Use "
            "add_pattern_controller (not arrangement clips). "
            "Keyframes: position 0 at value 0.0; position 96 at 0.4; "
            "position 192 at 0.8; position 288 at 1.0 (a clean fade-in "
            "across 4 beats at PPQ=96). channel_iid = the kick channel's "
            "iid (look it up with list_channels). Do not modify the "
            "existing note. Project path: "
        ),
        min_notes_added=0,
        min_controllers_added=4,
        min_grade=4,
        max_iterations=15,
        description=(
            "Exercises add_pattern_controller (0xDF encoder). Verifies "
            "the agent reads channel state, identifies the right "
            "channel_iid, and lands 4 keyframes with monotonic ramp."
        ),
    ),
    FullSongCase(
        id="create_channel_pattern_and_compose",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        user_prompt=(
            "Compose a new bassline. Steps you must perform: "
            "(1) create a new instrument channel named 'Bass'; "
            "(2) create a new pattern named 'Bassline'; "
            "(3) add at least 8 notes to the new pattern on the new "
            "channel. Notes should be in the bass register (C2..C3, FL "
            "keys 36..48), with melodic motion (don't repeat the same "
            "key 8 times). Use a quarter-note grid (positions 0, 96, "
            "192, ... at PPQ=96), each note one beat long. "
            "Do not modify the existing kick channel or its pattern. "
            "Project path: "
        ),
        min_notes_added=8,
        min_grade=4,
        max_iterations=20,
        expect_new_pattern=True,
        expect_new_channel=True,
        description=(
            "Hard case: exercises create_channel + create_pattern + "
            "add_pattern_note end-to-end. Validates that the agent "
            "doesn't merge work into the existing pattern/channel."
        ),
    ),
]
