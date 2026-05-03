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

## Capability gaps (what we need to add before this demo fully works)

### Already shipped (Phase 2.2 + 2.3)
- ✅ `set_channel_name`
- ✅ `set_insert_name`
- ✅ `set_pattern_name`
- ✅ `save` + `restore_snapshot`
- ✅ `get_pattern_steps` (read step grid per channel)

### Missing — small additions (~30 min each)
These all wrap a single FL API call that's already exposed in the
MIDI scripting surface (verified via `list_apis` probe on FL 2025).
Add as Phase 2.6 — Color + Routing kinds:

- ⏸️ `set_channel_color(iid, color)` — wraps `channels.setChannelColor`
- ⏸️ `set_insert_color(idx, color)` — wraps `mixer.setTrackColor`
- ⏸️ `set_pattern_color(iid, color)` — wraps `patterns.setPatternColor`
- ⏸️ `link_channel_to_insert(channel_iid, insert_idx)` — wraps
  `mixer.linkChannelToTrack`
- ⏸️ `clone_pattern(source_iid, new_name)` — wraps `patterns.clonePattern`

Each follows the existing dispatcher pattern (pydantic schema +
auto-snapshot + IPC handler in `device_flstudio_mcp.py`).

### Blocked — needs FL API extension or pyscript bridge
- ❌ Move pattern blocks between playlist tracks (no playlist
  manipulation API in `channels`/`patterns`/`mixer`/`general`/
  `transport`/`ui`/`plugins` modules per FL 2025)
- ❌ Add/delete playlist tracks
- ❌ Rename playlist tracks
- ❌ Color playlist tracks
- ❌ Read playlist arrangement (which patterns are on which tracks at
  what time positions)

The "make it look like Ableton" playlist rearrangement is the
**playlist-tracks** part, which is the part FL's MIDI scripting API
does not expose at all. To fully realize this demo we need either:
1. Image-Line to expose a playlist module (file an issue), or
2. A `flpianoroll`-style pyscript bridge for playlist manipulation
   (queued behind the piano-roll bridge work — decision #31)

For v0.5 the demo ships a **partial reorganize**: rename + color +
re-route at the channel/insert/pattern level. The visual playlist
tidy-up is left to the user.

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
