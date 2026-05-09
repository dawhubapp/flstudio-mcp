# Compose into this FL Studio project

You have access to one MCP tool: `offline_execute(kind, args)`. It
operates on a `.flp` file directly. Don't use `live_execute`.

Every kind takes `args = {"path": "<path>", ...}`.

**Read kinds:** `describe`, `list_channels`, `list_mixer`,
`list_patterns`, `list_arrangements`, `list_tracks`, `list_clips`,
`list_plugins`, `list_apis`.

**Discovery shortcuts (prefer over walking `list_*`):**
- `find_channel_by_name(path, query, fuzzy?)` — substring match
- `find_insert_by_name(path, query, fuzzy?)`
- `find_pattern_by_name(path, query, fuzzy?)`
- `find_plugin_instances(path, plugin_name)` — every instance of a
  plugin (channel + mixer scopes)

**Mutation kinds you will need:**

- `create_channel(path, name?, kind?)` — kind defaults to `sampler`.
  Returns `{channel_iid}`.
- `create_pattern(path, name?)` — returns `{pattern_id}`.
- `add_pattern_note(path, pattern_id, position, channel_iid, length, key, velocity?)`
  — append a single note. `position` + `length` in PPQ ticks
  (`describe.ppq` is the resolution; FL default = 96, so 1 beat = 96
  ticks, 1 bar 4/4 = 384). `key`: 0..131; **60 = C5** in FL's mapping
  (one octave higher than MIDI C4). `velocity`: 0..127, default 100.
- `set_pattern_notes(path, pattern_id, notes)` — replace every note in
  a pattern with the given list. Each note is the same shape as
  `add_pattern_note` (minus `path`/`pattern_id`).
- `remove_pattern_note(path, pattern_id, index)` — remove one note by
  position in the pattern's note list.
- `add_pattern_controller(path, pattern_id, position, channel, value, flags?)`
  — add one keyframe to a pattern-scoped automation curve (opcode
  0xDF). `position` in PPQ ticks; `channel` is the channel iid the
  automation targets; `value` is normalized 0..1 (float32, written
  raw). `flags` is an int (default 0). Multiple `add_pattern_controller`
  calls on the same channel build up a piecewise-linear curve at
  the keyframe positions.
- `set_pattern_controllers(path, pattern_id, controllers)` — replace
  every controller in a pattern with the given list. Each entry
  shape: `{position, channel, value, flags?}`.
- `remove_pattern_controller(path, pattern_id, index)` — remove one
  by index.
- `set_native_plugin_param(path, scope, param, value)` — patch one
  parameter of a Fruity native plugin's state. Currently supports
  `Fruity Parametric EQ 2` (use `param={kind: "main_level"}` or
  `{kind: "band", band: 1..7, field: "level"|"freq"|"width"}`),
  `Fruity Reeverb 2`, `Fruity Limiter` (use `param={kind: "param",
  index: N}` — find N from the plugin's UI param order). `value` is
  normalized 0..1.
- `set_pattern_length(path, pattern_id, ticks)` — write/update the
  pattern-length scalar (PPQ ticks). Useful when you need to grow a
  pattern past its current length.
- `transpose_pattern_notes(path, pattern_id, semitones, channel_iid?)`
  — shift every note's key by N semitones; optional channel filter.
- `quantize_pattern_notes(path, pattern_id, grid_ticks, strength?)`
  — snap positions to nearest grid multiple. `strength` 0..1
  (default 1 = full snap, 0.5 = halfway). Auto-grows pattern length.
- `humanize_velocities(path, pattern_id, range, seed?)` — add ±range
  jitter to velocities (clamped 1..127). For "make this groovier".
- `humanize_timings(path, pattern_id, range_ticks, seed?)` — add
  ±range_ticks position jitter. Auto-grows pattern length.
- `reverse_pattern_notes(path, pattern_id)` — mirror notes in time
  about pattern length.
- `invert_pattern_notes(path, pattern_id, axis_key?)` — mirror keys
  about `axis_key` (default 60 = C5).
- `set_channel_volume(path, iid, value)` — set a channel's volume
  slider. `value` normalized 0..1 (FL default 0.78). Use this for
  basic mixing ("turn the bass down 6dB" ≈ value 0.5 if it was at
  the default 0.78).
- `set_channel_pan(path, iid, value)` — set a channel's pan slider.
  `value` bipolar -1..+1 (-1 = full left, 0 = center, +1 = right).
- `arrange_song(path, arrangement, structure, track_index?, beats_per_bar?)`
  — lay out a sequence of pattern clips on one track. `structure` =
  `[{pattern_id, bars, position_ticks?}]`. Positions computed
  sequentially (`bars * beats_per_bar * ppq`) unless explicitly
  given. Default `track_index=0`, `beats_per_bar=4`.
- `instantiate_native_plugin(path, donor_path, plugin_name, insert_index, slot_marker)`
  — load a native FL plugin onto a fresh mixer slot by splicing from
  a donor FLP that already has it. Returns `{fl_ipc_slot_index =
  slot_marker + 1}`. Best-effort: FL UI recognition works; IPC
  binding may fail in some cases.

## Goal

Add the requested musical content to the project. Be specific and
musical — not random keys. If the user asks for a bassline, write
notes in a bass register (C2..C4, FL keys ~36..72). If they ask for a
melody, use C5..C6 range (FL keys ~60..84). Respect the project's
PPQ when computing positions.

When you're done, end your turn (no more tool calls). The user will
verify automatically.

## Approach

1. `describe` once (note ppq, existing patterns, channels).
2. Read `list_channels` + `list_patterns` to see what's there.
3. Plan the addition in one short paragraph (which pattern, which
   channel, how many notes, what range).
4. Execute: create channels/patterns first if needed, then add notes
   in order.
5. Stop.

## Worked examples

These show canonical workflows. Do NOT copy literally — adapt to the
user's actual prompt.

### Example A — "Add a 16-note melody to an empty channel"

```
1. offline_execute(describe) → ppq=96, 1 channel (Sampler iid=0), 0 patterns
2. offline_execute(create_channel, name="Lead") → {channel_iid: 1}
3. offline_execute(create_pattern, name="Lead Hook") → {pattern_id: 1}
4. offline_execute(set_pattern_notes, pattern_id=1, notes=[
     {position: 0, channel_iid: 1, length: 96, key: 60, velocity: 100, ...},
     {position: 96, channel_iid: 1, length: 96, key: 64, velocity: 105, ...},
     ... 14 more ...
   ])
5. End turn.
```

Key tips: batch via `set_pattern_notes` instead of 16 sequential
`add_pattern_note` calls (cheaper). Vary velocity (95–115) for
musicality. Stay in C5..C6 register (FL keys 60..84).

### Example B — "Tweak EQ 2 main level + add notes"

```
1. offline_execute(describe) → identify EQ 2 location
2. offline_execute(find_plugin_instances, plugin_name="EQ 2")
   → [{scope: "mixer", insert_index: 1, slot_index: 0, ...}]
3. offline_execute(create_pattern, name="Hook") → {pattern_id: 1}
4. offline_execute(set_pattern_notes, pattern_id=1, notes=[...4 notes...])
5. offline_execute(set_native_plugin_param,
     scope={kind: "mixer_slot", insert_index: 1, slot_index: 0},
     param={kind: "main_level"}, value=0.8)
6. End turn.
```

Key tips: use `find_plugin_instances` instead of walking
`list_mixer` to locate the plugin. Compose first, tweak last.

### Example C — "Lay 3 patterns into a song" (via add_clip)

```
1. offline_execute(list_arrangements) → [{id: 0, ...}]
2. offline_execute(list_patterns) → [{id: 1, name: "Verse"}, {id: 2, name: "Chorus"}]
3. offline_execute(add_clip, arrangement_id=0, kind="pattern",
     ref_id=1, track_index=0, position_ticks=0, length_ticks=384)
4. offline_execute(add_clip, ..., position_ticks=384, ref_id=2, ...)
5. offline_execute(add_clip, ..., position_ticks=768, ref_id=1, ...)
6. End turn.
```

Key tips: PPQ=96 default → 1 bar 4/4 = 384 ticks. Position is
absolute in the arrangement timeline. Track 0 = top track.
