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

from ..harness.invariants_song import (
    ChannelLevelExpectation,
    ChannelSamplePathExpectation,
    PluginParamExpectation,
    VisualGateExpectation,
)

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
    max_output_tokens_per_turn: int = 4096
    # Default agent + judge models per project standard: opus drives,
    # sonnet grades. Cases override per-test if needed.
    model: str = "claude-opus-4-7"
    judge_model: str = "claude-sonnet-4-6"
    description: str = ""
    expect_new_pattern: bool = False
    expect_new_channel: bool = False
    expect_plugin_params: list[PluginParamExpectation] = field(default_factory=list)
    expect_channel_levels: list[ChannelLevelExpectation] = field(default_factory=list)
    expect_channel_sample_paths: list[ChannelSamplePathExpectation] = field(default_factory=list)
    visual_gates: list[VisualGateExpectation] = field(default_factory=list)
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
        id="techno_loop_demo",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_empty.flp",
        user_prompt=(
            "Build a 4-bar techno loop at 128 BPM from this empty project. "
            "You have FL Studio's full factory drum library available via "
            "list_factory_samples. Workflow: "
            "(1) set_tempo(bpm=128). "
            "(2) Use list_factory_samples(category='Drums', query='kick') "
            "to find a punchy techno kick (909/707/808). Pick one. "
            "(3) The default Sampler channel (iid=0) is empty. Reuse it: "
            "set_channel_name to 'Kick', set_channel_color (kick should "
            "be red-ish), set_channel_sample_path to your chosen kick token, "
            "set_channel_routing to insert 1. "
            "(4) Repeat for snare (find via category='Drums', query='snare'; "
            "warm orange color; route to insert 2), closed hat (query='hat' "
            "or 'hh'; yellow; insert 3), open hat (different sample if "
            "available; lighter yellow; insert 4). Use create_channel for "
            "each new one. "
            "(5) Create a pattern named 'Beat'. Add a classic 4-on-the-floor "
            "techno pattern at PPQ=96, 4 bars long (1536 ticks). "
            "IMPORTANT: build all notes for the pattern in ONE single "
            "set_pattern_notes(path, pattern_id, notes=[...]) call with "
            "the full array — do NOT call add_pattern_note per note (that "
            "burns output tokens and will hit the cap before you finish). "
            "Compute positions programmatically in the notes array: "
            "  - Kick (channel_iid=0): every quarter note. "
            "    positions = [0, 96, 192, 288, 384, 480, 576, 672, 768, 864, 960, 1056, 1152, 1248, 1344, 1440] "
            "    length=96, velocity=110, key=60. "
            "  - Snare (channel_iid=1): on beats 2 and 4 of each bar. "
            "    positions = [96, 288, 480, 672, 864, 1056, 1248, 1440] "
            "    length=48, velocity=100, key=60. "
            "  - Closed hat (channel_iid=2): every 8th note OFF the kick. "
            "    positions = [48, 144, 240, 336, 432, 528, 624, 720, 816, 912, 1008, 1104, 1200, 1296, 1392, 1488] "
            "    length=24, velocity=80, key=60. "
            "  - Open hat (channel_iid=3): sparse accents. "
            "    positions = [240, 624, 1008, 1392] "
            "    length=48, velocity=90, key=60. "
            "Concatenate all into one notes array (~44 notes total) and "
            "submit via a single set_pattern_notes call. "
            "(6) Mix balance: set_channel_volume — kick 0.85, snare 0.75, "
            "closed hat 0.55, open hat 0.6. "
            "(7) arrange_song(arrangement=0, structure=[{pattern_id: <id>, "
            "bars: 4}, {pattern_id: <id>, bars: 4}, {pattern_id: <id>, "
            "bars: 4}, {pattern_id: <id>, bars: 4}]) — 16 bars total. "
            "End. Project path: "
        ),
        min_notes_added=24,  # 16 kick + 8 snare minimum (relaxed from full ~56)
        min_grade=4,
        max_iterations=30,
        max_input_tokens=600_000,
        expect_new_pattern=True,
        expect_new_channel=True,  # snare, hat, hat = at least 3 new
        expect_channel_sample_paths=[
            ChannelSamplePathExpectation(iid=0, must_differ=True, new_substring=".wav"),
        ],
        expect_channel_levels=[
            ChannelLevelExpectation(
                iid=0,
                field="volume",
                min_normalized=0.7,
                max_normalized=0.95,
            ),
        ],
        description=(
            "FULLY AUTONOMOUS techno loop demo (Variant A). Agent builds "
            "a 4-bar 128 BPM techno groove from base_empty.flp with no "
            "human input on composition. Uses sample browser + channel "
            "creation + pattern notes + mixing + arrange_song end-to-end. "
            "Drum-driven only — no synth/automation gaps."
        ),
    ),
    FullSongCase(
        id="swap_kick_sample",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_one_pattern.flp",
        user_prompt=(
            "The Kick channel (iid=1) currently uses a 909 Kick sample. "
            "Replace it with a different vintage drum kick from the FL "
            "factory library. Steps: (1) call list_factory_samples with "
            "category='Drums' and query='kick' to see what's available; "
            "(2) pick one that's NOT the current 909 sample (try 707, "
            "808, Linn, or similar); (3) call set_channel_sample_path "
            "to swap it in. Don't add or remove notes. Project path: "
        ),
        min_notes_added=0,
        min_grade=4,
        max_iterations=10,
        expect_channel_sample_paths=[
            ChannelSamplePathExpectation(iid=1, must_differ=True),
        ],
        description=(
            "Exercises list_factory_samples + set_channel_sample_path "
            "(F7.1). Verifies the agent can browse the factory library "
            "and swap a sample on an existing channel."
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
    FullSongCase(
        id="house_song_multipattern",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_empty.flp",
        user_prompt=(
            "Build a 32-bar deep-house arrangement at 124 BPM from this "
            "empty project. PPQ=96, so 1 bar = 384 ticks, 4 bars = 1536 ticks. "
            "Two patterns alternate to create verse/drop dynamics. "
            "STRATEGY (critical for token budget): "
            "(a) for each pattern, build the full notes array in ONE "
            "set_pattern_notes(path, pattern_id, notes=[...]) call. Do "
            "NOT use add_pattern_note per-note. "
            "(b) Channel setup MUST batch in PARALLEL tool calls — issue "
            "all create_channel + set_channel_sample_path + "
            "set_channel_color + set_channel_volume + set_channel_routing "
            "calls in a SINGLE message (multiple tool_use blocks). "
            "Sequential per-channel iterations burn the per-turn output "
            "budget. "
            "(c) Patterns may go in a second message; arrangement in a "
            "third. Target: full song in ≤4 model turns. "
            "(1) set_tempo(bpm=124). "
            "(2) Set up 5 channels (use list_factory_samples for each, "
            "category='Drums' or 'Bass'): "
            "  - iid=0 (default Sampler): rename 'Kick', red color, "
            "    set_channel_sample_path to a deep house kick (909/707 "
            "    style), volume 0.85, route to insert 1. "
            "  - create_channel 'Snare': orange, snare/clap sample, "
            "    volume 0.7, insert 2. "
            "  - create_channel 'Hat': yellow, closed hat sample, "
            "    volume 0.75, insert 3. "
            "  - create_channel 'Bass': blue, sub-bass or bass-loop "
            "    sample (try query='bass' or 'sub'), volume 0.75, "
            "    insert 4. "
            "  - create_channel 'Stab': purple/magenta, chord/stab "
            "    sample (try query='chord' or 'stab' or 'piano'), "
            "    volume 0.6, insert 5. "
            "(3) create_pattern 'Verse' (4 bars, sparse). Build one "
            "    set_pattern_notes call with: "
            "    - Kick (channel_iid=0): 4-on-floor, positions [0, 96, "
            "      192, 288, 384, 480, 576, 672, 768, 864, 960, 1056, "
            "      1152, 1248, 1344, 1440], length=96, velocity=105, "
            "      key=60. (16 notes) "
            "    - Hat (channel_iid=2): off-beat 8ths, positions [48, "
            "      144, 240, 336, 432, 528, 624, 720, 816, 912, 1008, "
            "      1104, 1200, 1296, 1392, 1488], length=24, "
            "      velocity=110, key=60. (16 notes) "
            "    Total verse = 32 notes. "
            "(4) create_pattern 'Drop' (4 bars, full). Build one "
            "    set_pattern_notes call with: "
            "    - Kick (iid=0): same 16 positions as verse, "
            "      velocity=115. "
            "    - Snare (iid=1): backbeat, positions [96, 288, 480, "
            "      672, 864, 1056, 1248, 1440], length=48, "
            "      velocity=100, key=60. (8 notes) "
            "    - Hat (iid=2): same 16 positions as verse, "
            "      velocity=115. "
            "    - Bass (iid=3): walking bass, positions [0, 96, 192, "
            "      288, 384, 480, 576, 672, 768, 864, 960, 1056, 1152, "
            "      1248, 1344, 1440] with keys cycling [72, 72, 75, "
            "      72, 72, 72, 77, 74, 72, 72, 75, 72, 72, 72, 77, 74] "
            "      (C5 root — that's FL's native sample pitch — with "
            "      motion to D#5/F5/D5; using key=36 here would play "
            "      the sample 2 octaves below its native root and "
            "      sound like sub-rumble), length=96, velocity=100. "
            "      (16 notes) "
            "    - Stab (iid=4): chord hits on backbeat, positions "
            "      [96, 288, 480, 672, 864, 1056, 1248, 1440], "
            "      length=48, velocity=100, key=60. (8 notes) "
            "    Total drop = 64 notes. "
            "(5) arrange_song(arrangement=0, structure=[ "
            "      {pattern_id: <Verse>, bars: 4}, "
            "      {pattern_id: <Verse>, bars: 4}, "
            "      {pattern_id: <Drop>,  bars: 4}, "
            "      {pattern_id: <Drop>,  bars: 4}, "
            "      {pattern_id: <Verse>, bars: 4}, "
            "      {pattern_id: <Drop>,  bars: 4}, "
            "      {pattern_id: <Drop>,  bars: 4}, "
            "      {pattern_id: <Drop>,  bars: 4} "
            "    ]) — 32 bars total, V-V-D-D-V-D-D-D structure. "
            "End. Project path: "
        ),
        min_notes_added=80,  # 32 + 64 = 96; relax to 80 for partial credit
        min_grade=4,
        max_iterations=30,
        max_input_tokens=600_000,
        max_output_tokens_per_turn=8192,  # set_pattern_notes(64 notes) is large
        expect_new_pattern=True,
        expect_new_channel=True,
        expect_channel_sample_paths=[
            ChannelSamplePathExpectation(iid=0, must_differ=True, new_substring=".wav"),
        ],
        expect_channel_levels=[
            ChannelLevelExpectation(
                iid=0,
                field="volume",
                min_normalized=0.7,
                max_normalized=0.95,
            ),
        ],
        visual_gates=[
            VisualGateExpectation(
                name="playlist_has_clips",
                question=(
                    "Does the FL Studio playlist (timeline) view show "
                    "multiple non-empty clip blocks laid out across the "
                    "first several bars? Empty playlists / zero-length "
                    "clips do NOT count as PASS."
                ),
            ),
        ],
        description=(
            "Multi-pattern deep house arrangement. Two 4-bar patterns "
            "(Verse sparse, Drop full) alternate across 32 bars. "
            "Validates: multi-pattern composition, melodic bass lines "
            "(varied keys not just key=60), sort-fix correctness across "
            "5 channels, and arrangement structure repeating patterns. "
            "Stress-test for the bug fixed in flpdiff 6cd7e15."
        ),
    ),
    FullSongCase(
        id="compose_with_dx10_bass",
        input_flp=CORPUS_DIR / "re_base" / "fl25" / "base_empty.flp",
        user_prompt=(
            "Build a 4-bar bassline at 120 BPM using a real Fruity DX10 "
            "preset (NOT a sample channel). Workflow: "
            "(1) set_tempo(bpm=120). "
            "(2) list_factory_presets(plugin='DX10', kind='generator') to "
            "discover the available DX10 presets. Pick a bass-sounding "
            "one (Chunky Bass, Bouncy Bass, anything with 'bass' in the "
            "name); if none, just pick the first DX10 preset. "
            "(3) load_factory_preset(path, fst_path=<picked path>, "
            "kind='generator', name='Bass') — that returns "
            "{channel_iid} for the freshly-loaded DX10 channel. "
            "(4) create_pattern(name='Bassline'). "
            "(5) set_pattern_notes(path, pattern_id=<new id>, notes=[...]) "
            "with 16 notes targeting the new channel_iid. DX10 is a "
            "native plugin (not a sample channel), so it plays at "
            "WRITTEN pitch — use musical bass-octave keys 36..47 "
            "(C2..B2). Quarter-note grid for a 4-bar pattern at PPQ=96 "
            "= positions [0, 96, 192, 288, 384, 480, 576, 672, 768, "
            "864, 960, 1056, 1152, 1248, 1344, 1440]. Vary the keys "
            "across [36, 36, 39, 36, 41, 36, 39, 36, 36, 36, 39, 36, "
            "41, 38, 39, 36] for a walking bassline; length=96, "
            "velocity=100. "
            "(6) arrange_song(arrangement=0, structure=[{pattern_id: "
            "<new id>, bars: 4}]) — single 4-bar clip on the playlist. "
            "End. Project path: "
        ),
        min_notes_added=16,
        min_grade=4,
        max_iterations=15,
        max_input_tokens=400_000,
        max_output_tokens_per_turn=8192,
        expect_new_pattern=True,
        expect_new_channel=True,
        description=(
            "F9.6 — first e2e that loads a native FL plugin onto a "
            "fresh channel via load_factory_preset and writes notes "
            "for it. Validates list_factory_presets discovery + .fst "
            "splice + note targeting end-to-end on a single agent run."
        ),
    ),
]
