# Reorganize this FL Studio project — Ableton-style

You have access to two MCP tools that operate on `.flp` files:

- `offline_execute(kind, args)` — read + mutate a `.flp` file directly.
- `live_execute(kind, args)` — drive a running FL Studio. **Don't use
  this** for offline reorganize tasks.

Use `offline_execute` exclusively. Every read kind takes
`args = {"path": "<path>"}`. Mutation kinds also take `path` plus the
field-specific args.

**Read kinds:** `describe`, `list_channels`, `list_mixer`,
`list_patterns`, `list_arrangements`, `list_tracks`, `list_clips`,
`list_plugins`, `list_apis`.

**Mutation kinds you will need:**

- `set_channel_name(path, iid, name)`
- `set_channel_color(path, iid, color={r,g,b})`
- `set_channel_routing(path, iid, target_insert)`
- `set_insert_name(path, index, name)`
- `set_insert_color(path, index, color={r,g,b})`
- `set_pattern_name(path, iid, name)`
- `set_pattern_color(path, iid, color={r,g,b})`
- `set_track_name(path, arrangement_id, track_index, name)`
- `set_track_color(path, arrangement_id, track_index, color={r,g,b})`

## Goal

Bring the project to Ableton-style cleanliness:

1. **Names**: every channel, insert, pattern, and used track gets a
   *content-based* name. "Kick", "Bass Sub", "Lead Synth" — not
   "Sample 1", "Track 1", "Insert 1".

2. **Routing**: every active (non-disabled) channel routes to its own
   dedicated mixer insert (one channel → one insert). No channels on
   the Master (insert 0).

3. **Colors**: pick one color *per content group* from the palette:

   | Group | r,g,b | Use for |
   |---|---|---|
   | Drums (kick, snare) | 233, 75, 60 | hard percussion |
   | Drums (hat, perc) | 255, 140, 66 | softer percussion |
   | Bass | 59, 130, 246 | basslines, sub |
   | Lead/Melody | 34, 197, 94 | melodic synths |
   | Pads/Atmo | 139, 92, 246 | pads, atmospheres |
   | FX/Risers | 236, 72, 153 | risers, sweeps, impacts |
   | Vocals | 250, 204, 21 | vocal stems |

   Apply the chosen color consistently across the channel + its mixer
   insert + the pattern that uses it. Tracks that play those clips
   should match.

4. **Preservation**: do **not** add or remove clips, do **not** edit
   notes. This is a metadata + routing pass.

## Approach

Start by inspecting: call `describe` once, then `list_channels`,
`list_mixer`, `list_patterns`, `list_arrangements`, and (per
arrangement) `list_tracks` + `list_clips`. Plan all mutations
*before* applying them — name your plan in a single short paragraph
of text, then execute.

When you're done, end your turn (no more tool calls). The user will
verify automatically.
