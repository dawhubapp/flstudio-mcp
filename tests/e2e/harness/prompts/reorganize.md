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

**Discovery shortcuts** (cheaper than walking `list_*`):
- `find_channel_by_name(path, query, fuzzy?)` — substring match
- `find_insert_by_name(path, query, fuzzy?)`
- `find_pattern_by_name(path, query, fuzzy?)`
- `find_plugin_instances(path, plugin_name)` — every instance of a
  plugin (channel + mixer scopes)

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

## Worked example — 4-channel synthetic walkthrough

Project state: 4 unnamed channels (Sample 1..4), all on Master, no
patterns named, default colors. Approach:

```
1. offline_execute(describe) + list_channels + list_mixer
   → identify channels by sample_path: 909-Kick, 909-Snare, 909-HH, 808-Bass
2. Plan: drum group (red/orange) + bass (blue). 4 separate inserts.
3. Apply (parallel where possible):
   - set_channel_name(iid=0, name="Kick")
   - set_channel_name(iid=1, name="Snare")
   - set_channel_name(iid=2, name="HH")
   - set_channel_name(iid=3, name="Bass")
   - set_channel_color(iid=0, color={r: 233, g: 75, b: 60})  # drums hard
   - set_channel_color(iid=1, color={r: 233, g: 75, b: 60})
   - set_channel_color(iid=2, color={r: 255, g: 140, b: 66})  # drums soft
   - set_channel_color(iid=3, color={r: 59, g: 130, b: 246})  # bass
   - set_channel_routing(iid=0, target_insert=1)
   - set_channel_routing(iid=1, target_insert=2)
   - set_channel_routing(iid=2, target_insert=3)
   - set_channel_routing(iid=3, target_insert=4)
   - set_insert_name(index=1, name="Kick"), color matches channel
   - ...same pattern for inserts 2..4
4. End turn.
```

Key tips: identify content by `sample_path`, not by default name.
Group by content (drums share warm hues, bass = blue). Routing is
1:1 (one channel → one insert).
