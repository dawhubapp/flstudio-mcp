# Demo 3 — Reorganize the project (cleanup pass)

> **Status:** SPEC + PROMPT TEMPLATE. Fully realizing this demo
> requires capability extensions noted below. The mixer-coloring +
> naming + auto-routing portion ships in v0.5; the playlist
> rearrangement portion is blocked by an FL API gap and is queued for
> v1.x via the `flpianoroll`-style pyscript bridge (or upstream FL API
> change).

## Why this demo matters

FL Studio is built for fast/messy creativity. By bar 32 most projects
have:
- Channels named "Sample 1", "Sample 2", "Kick (1)", "Kick (1) #2", …
- Mixer inserts at default labels with default routing (everything to
  Master)
- Playlist tracks all "Track 1, Track 2, …", patterns scattered
  across them
- No color coding — visually impossible to find anything

A "reorganize" pass turns that mess into a clean, navigable session
the producer can keep building on. This is exactly the kind of
tedious-but-necessary task an AI agent should own.

---

## The prompt (paste into Claude Desktop / Cursor)

> **You:** Clean up my FL Studio project. I want it to look like an
> Ableton session: one sample per mixer track, named after the sound,
> and color-coded by group (drums = warm, bass = blue, melodic =
> green, FX = purple). Patterns should be named after their role and
> color-coded the same way. Auto-route every channel-rack channel to
> its own dedicated mixer insert (link channel → insert) so I can mix
> each sound independently. Don't move any notes — just rename, color,
> and re-route. Snapshot before you start so I can roll back. Show me
> what you're about to do before doing it.

Expected behavior chain:

1. `live_execute(kind="describe")` — get current state (channel count,
   pattern count, insert count).
2. `live_execute(kind="get_pattern_steps", args={...})` for each
   channel — discover which channels actually carry steps so the agent
   knows what's musically active vs. dead weight.
3. **Plan the rename + color scheme** in a natural-language preview
   ("Channel 0 'Sample 1' looks like a kick — propose: name=Kick,
   color=red, route to Insert 1 'Drums Kick'"). Wait for user
   confirmation.
4. `live_execute(kind="save")` first → snapshot pre-cleanup.
5. For each channel: `set_channel_name`, `set_channel_color`,
   `link_channel_to_insert`.
6. For each touched insert: `set_insert_name`, `set_insert_color`.
7. For each pattern: `set_pattern_name`, `set_pattern_color`.
8. `live_execute(kind="save")` again → final state snapshot.
9. Report what changed. Offer `restore_snapshot(<pre>)` if user wants
   to undo.

---

## Capability map

This demo runs **fully offline** through `offline_execute` — FL
Studio doesn't need to be open. The offline path bypasses FL's
playlist-API gap entirely by mutating the `.flp` file's bytes
directly via the canonical TS parser + serializer.

### Live + offline (`live_execute` + `offline_execute`, Phase 2.2 + 2.3)
- ✅ `set_channel_name`, `set_insert_name`, `set_pattern_name`
- ✅ `save` + `restore_snapshot`
- ✅ `get_pattern_steps` (read step grid per channel — live only)

### Offline-only (Phase 3.4 — this is where the demo lives)
- ✅ Reads: `describe`, `list_channels`, `list_mixer`, `list_patterns`,
  `list_arrangements`, `list_tracks`, `list_clips` — full project
  introspection without launching FL.
- ✅ Names: `set_arrangement_name`, `set_track_name`,
  `set_pattern_name`, `set_channel_name`, `set_insert_name`.
- ✅ Colors: `set_channel_color`, `set_insert_color`,
  `set_pattern_color`, `set_track_color`.
- ✅ Routing: `set_channel_routing(iid, target_insert)` — sends a
  channel to a specific mixer insert.
- ✅ Pattern lifecycle: `clone_pattern(source_iid, name?)` — duplicates
  notes + controllers + name + color + length.
- ✅ Playlist clips: `add_clip(arrangement, kind, ref_id, track_index,
  position_ticks, length_ticks)`, `remove_clip(arrangement, match)`,
  `move_clip(arrangement, match, {to_track_index?, to_position_ticks?})`.

### Why offline beats live for this demo
1. **No playlist API gap.** FL 2025's MIDI scripting surface exposes
   nothing for playlist tracks — no add/delete/rename/color/move.
   Offline mode writes the bytes directly.
2. **No FL window required.** The user doesn't have to launch FL,
   wait for it to open, or have it on screen during the rewrite.
3. **Auto-snapshot before every write.** Each mutation snapshots
   the file first; `restore_snapshot` rolls back any single op.
4. **Round-trip safe.** All 105 corpus FLPs (including 100+ personal
   projects) survive `parse → serialize → parse` byte-exact.

### Known limits (not blockers for this demo)
- ⏸️ Channel/insert color via the live API (`channels.setChannelColor`,
  `mixer.setTrackColor`, `patterns.setPatternColor`,
  `mixer.linkChannelToTrack`) — Phase 2.6, not yet wired. **Live mode
  doesn't need them since the offline path covers the demo.**
- ⏸️ Live `link_channel_to_insert` wraps `mixer.linkChannelToTrack` —
  same comment.
- ❌ Per-clip color, fade, stretch — out of scope; FL's clip records
  expose flags + offsets but no clip-level color.

---

## Color scheme suggested in the prompt

The agent picks colors per group from a stable palette. Suggested
mapping (FL Studio uses 0xAARRGGBB int colors via these APIs):

| Group | Hex | Use for |
|-------|-----|---------|
| Drums (kick, snare) | `0xFFE94B3C` (warm red) | hard/percussive |
| Drums (hat, perc) | `0xFFFF8C42` (orange) | softer percussion |
| Bass | `0xFF3B82F6` (blue) | basslines, sub |
| Lead/Melody | `0xFF22C55E` (green) | melodic synths |
| Pads/Atmo | `0xFF8B5CF6` (purple) | pads, atmospheres |
| FX/Risers | `0xFFEC4899` (pink) | risers, sweeps, impacts |
| Vocals | `0xFFFACC15` (yellow) | vocal stems |

The agent should pick a group per channel by inspecting the channel
name (regex on common keywords: `kick|snare|hat|clap|perc|bass|sub|
lead|pad|atmo|fx|riser|vocal|vox`) and ask the user when ambiguous.

---

## How to capture this demo

1. Wait until v0.6+ when the color + routing kinds ship.
2. Open a deliberately-messy FL project (`Sample 1 → Sample 17`,
   default mixer state, no colors).
3. Send the prompt above.
4. Capture each tool call's structured envelope + Claude's
   natural-language summaries.
5. Take FL screenshots before + after — the visual diff is the
   payoff of this demo.
6. Commit as `docs/examples/03-reorganize-project-transcript.md`.

---

## Skill packaging

This prompt is a strong candidate to ship as a Claude **Skill** later:

- Skill name: `fl-studio:reorganize-project`
- Trigger: user says "clean up my FL project" / "reorganize this" /
  "make this look like Ableton" / "tidy up channels and mixer"
- Skill body: the 9-step chain above + the color-scheme palette + a
  preview-before-acting policy
- Required tools: `flstudio:live_execute` with the v0.6 kind set
