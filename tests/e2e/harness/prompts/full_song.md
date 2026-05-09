# Compose into this FL Studio project

You have access to one MCP tool: `offline_execute(kind, args)`. It
operates on a `.flp` file directly. Don't use `live_execute`.

Every kind takes `args = {"path": "<path>", ...}`.

**Read kinds:** `describe`, `list_channels`, `list_mixer`,
`list_patterns`, `list_arrangements`, `list_tracks`, `list_clips`,
`list_plugins`, `list_apis`.

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
- `set_native_plugin_param(path, scope, param, value)` — patch one
  parameter of a Fruity native plugin's state. Currently supports
  `Fruity Parametric EQ 2` (use `param={kind: "main_level"}` or
  `{kind: "band", band: 1..7, field: "level"|"freq"|"width"}`),
  `Fruity Reeverb 2`, `Fruity Limiter` (use `param={kind: "param",
  index: N}` — find N from the plugin's UI param order). `value` is
  normalized 0..1.

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
