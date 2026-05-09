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
