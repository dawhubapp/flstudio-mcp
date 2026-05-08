# flstudio-mcp — tools reference

Every tool flstudio-mcp exposes, every `kind` it dispatches, every arg
shape, with example envelopes. Auto-generated from the Pydantic schemas
in `src/flstudio_mcp/tools/_mutations.py` and the dispatcher tables in
`tools/live.py` + `tools/offline.py`.

## Tool surface

| MCP tool | Purpose | Requires FL running |
|----------|---------|---------------------|
| `live_execute` | Talk to a running FL Studio instance over IAC + the bundled MIDI script. Read + mutate + setup. | yes (except setup kinds) |
| `offline_execute` | Read + mutate `.flp` files on disk, no FL needed. Subprocess on the canonical TS parser/serializer. | no |

`live_execute` and `offline_execute` are deliberately namespaced apart
so the LLM can pick the right path. Read kinds with the same name
(`describe`, `list_channels`, …) return the same shape on both sides
so prompts can fall back transparently.

## Envelope shape

Every call returns:

```json
{
  "ok":         true | false,
  "kind":       "<echoed-kind>",
  "result":     <kind-specific> | { "error": "<CODE>", "message": "...", "hint": "...", "extra": {...} },
  "duration_ms": <number>,
  "log_id":     "<12-hex>"
}
```

`ok=false` always shapes `result` as an error object.

## Error taxonomy

| Code | Meaning | Typical fix |
|------|---------|-------------|
| `INVALID_ARGS` | Args failed Pydantic validation OR bridge rejected them | Fix args per the schema below |
| `UNSUPPORTED_KIND` | `kind` not in this tool's dispatcher | Use `list_apis` to enumerate |
| `FL_NOT_RUNNING` | Live call requires FL but no IPC handshake | Launch FL, open a project |
| `FL_DIALOG_BLOCKING` | Offline write attempted on a file FL has open | Close project in FL, retry |
| `IAC_DRIVER_OFFLINE` | macOS IAC bus disabled | Run `enable_iac` or use Audio MIDI Setup |
| `SCRIPT_NOT_INSTALLED` | MIDI script missing from FL Hardware/ | Run `install_script` |
| `HARDWARE_DIR_MISSING` | FL Studio 25 not installed | Install FL |
| `SNAPSHOT_FAILED` | Pre-write snapshot couldn't be taken | Check disk space, source file existence |
| `LEGACY_TEMPO_FORMAT` | Pre-FL-3.4.0 file uses 0x42/0x5D coarse+fine tempo | File too old for `set_tempo` writes |
| `EVENT_NOT_FOUND` | Mutation target doesn't exist (channel iid, pattern iid, track index, …) | Verify with a `list_*` read first |
| `UNKNOWN` | Catch-all (bridge subprocess crash, missing flpdiff, …) | See `result.message` + `result.hint` |

---

# `live_execute` reference

Call shape:

```python
live_execute(kind="<kind>", args={...})
```

## Read kinds

### `describe`
**Args:** none.
**Returns:** full FL project JSON — same shape as the offline `describe`.
Includes `metadata`, `channels[]`, `inserts[]`, `patterns[]`,
`arrangements[]`, plus FL-runtime fields (`current_filename`).

### `get_tempo`
**Args:** none. **Returns:** `{tempo_bpm: float}`.

### `list_channels`, `list_mixer`, `list_patterns`, `list_plugins`
**Args:** none. Same shape as the offline equivalents (see below).

### `get_pattern_steps`
**Args:** `{channel: int (≥0), count: int (1..256, default 16)}`.
**Returns:** `{channel, steps: bool[]}` — current pattern's step grid
for the named channel (only meaningful for step-sequencer mode channels).

### `get_plugin_info`
**Args:** `{index: int (≥0), scope: "channel" | "mixer" (default channel), slot: int (default -1)}`.
**Returns:** plugin metadata + parameter list for the named plugin.

### `list_apis`
**Args:** none. **Returns:** `{tool, kinds: string[], note}`.

## Write kinds (auto-snapshot before mutation)

### `set_tempo` — `{bpm: float (0 < bpm < 10000)}`
### `set_time_signature` — `{num: int (1..64), beat: int (1..64)}`
### `set_channel_volume` — `{iid: int (≥0), value: float (0..1)}`
### `set_channel_name` — `{iid: int (≥0), name: str (1..256)}`
### `set_insert_volume` — `{idx: int (≥0, 0=Master), value: float (0..1)}`
### `set_insert_name` — `{idx: int (≥0), name: str (1..256)}`
### `set_pattern_name` — `{iid: int (≥1, 1-based!), name: str (1..256)}`
> Pattern numbering is 1-based in both FL UI and API. `iid=0` silently
> no-ops at the script level — schema rejects it up front.
### `set_plugin_param` — `{index: int (≥0), param: int (≥0), value: float (0..1), scope: "channel"|"mixer", slot: int (-1 default; required ≥0 when scope="mixer")}`
### `set_mixer_eq` — `{idx: int (≥0), band: int (0=low,1=mid,2=high), frequency?: float, gain?: float, bandwidth?: float}`
### `save` — no args. Writes the project to disk via FL.
### `set_step` — `{channel: int (≥0), step: int (0..255), on: bool}` — toggle a step in the active pattern's step grid.
### `clear_pattern_steps` — `{channel: int (≥0), count: int (1..256, default 16)}` — clear N steps on a channel.

## Recovery

### `restore_snapshot` — `{snapshot_id: str}`
Roll back to a snapshot recorded by any prior write call. The
`snapshot_id` is in every successful write envelope's
`result.snapshot_id`.

## Setup kinds (don't require FL running)

### `install_script` — `{force: bool (default false)}`
Drop the bundled MIDI script into FL's Hardware directory. Idempotent
(sha256 sidecar check). `force=true` re-copies even if already present.

### `check_iac`
**Returns:** `{state: "online"|"offline"|"missing", devices: [...], detail: "..."}`.

### `enable_iac`
Drive Audio MIDI Setup via UI scripting to enable the IAC Driver.
Requires Accessibility permission for the calling process.

### `verify_setup`
Whole-chain check: IAC online + script installed + FL running + IPC
handshake. **Returns** per-check `{ok, detail}` map. Use this as the
first call in any new session.

### `reload_script`
Best-effort reload of the FL MIDI script via Settings → Update MIDI
scripts (pyautogui-driven). **Note:** confirmed in decision #32 that FL
2025 does not actually reload running scripts — only restart-FL works.
Kept for documentation purposes.

### `wire_input` (experimental)
pyautogui automation of the FL Settings → MIDI input → Enable toggle.
Coordinates work; FL UI semantics fragile. Manual is easier.

---

# `offline_execute` reference

Call shape:

```python
offline_execute(kind="<kind>", args={"path": "/abs/path.flp", ...})
```

`path` is required for every kind except `list_apis`. Subprocess to the
canonical TS parser/serializer (~25 ms cold per call). FL doesn't need
to be running, but **writes refuse with `FL_DIALOG_BLOCKING` if FL has
the file open** — its in-memory state would silently overwrite the edit.

## Read kinds

### `describe(path)`
Full FLP project JSON. `metadata` (title, artists, version, tempo,
time_signature, ppq, …), `channels[]`, `inserts[]`, `patterns[]`,
`arrangements[]`. Mirrors Python `flp-info --format json`.

### `get_tempo(path)` → `{tempo_bpm: float}`

### `list_channels(path)`
Per-channel summary: `iid`, `kind` (sampler/instrument/automation/...),
`name?`, `color?`, `levels` (pan, volume, pitch, filter), `enabled`,
`pingPongLoop`, `locked`, `plugin?` (name + vendor for VST-wrapped).

### `list_mixer(path)`
Per-insert summary: `index` (0=master), `name?`, `color?`, `flags`,
`slots[]` with per-slot plugin info, `volume?`, `pan?`, `stereoSeparation?`.

### `list_patterns(path)`
Per-pattern: `id`, `name?`, `notes[]` (24-byte note records:
position, length, key, channel_iid, velocity, mod_x, mod_y, …),
`controllers[]`, `color?`, `length?`, `looped?`.

### `list_plugins(path)`
Flat array of every plugin in the project, each tagged with `scope`
("channel" or "mixer"), `channel_index` or `(insert_index, slot_index)`,
`name`, `vendor`.

### `list_arrangements(path)`
**Returns:** `[{id, name, track_count, clip_count, timemarker_count}]`.

### `list_tracks(path, arrangement: int (default 0))`
**Returns:** `{arrangement, total_tracks, tracks: [...]}`. Tracks are
filtered to user-customised only (named OR locked OR disabled) — FL
emits 500 default tracks per arrangement, surfacing all of them
flooding LLM context.

Per-track shape: `{index, iid, name|null, color|null, enabled|null,
locked|null, height|null}`.

### `list_clips(path, arrangement: int (default 0))`
**Returns:** array of `{position_ticks, length_ticks, track_index
(un-reversed; 0=top), kind: "pattern"|"channel", ref_id, group, flags}`.
`ref_id` resolves to pattern.id or channel.iid based on
`pattern_base = 20480` discriminator.

### `list_apis()`
Enumerate all kinds. **Args:** `path` not required.

## Write kinds (auto-snapshot before mutation, FL-open lock-checked)

Each write envelope embeds `snapshot_id` in `result` for
`live_execute(kind="restore_snapshot", args={snapshot_id})` rollback.

### Project-level
- **`set_tempo(path, bpm: number)`** — replaces the modern `0x9C` u32
  milli-BPM event. Throws `LEGACY_TEMPO_FORMAT` on pre-FL-3.4.0 files
  (`0x42` u16 coarse + optional `0x5D` u16 fine).
- **`set_time_signature(path, numerator: int (1..255), denominator: int)`** —
  denominator must be a power of 2 in `[1, 64]` (1, 2, 4, 8, 16, 32, 64)
  matching FL UI options. Replaces top-level `0x11` + `0x12` u8 events.

### Channels (iid 0-based, sparse — FL preserves iids when middle channels deleted)
- **`set_channel_name(path, iid, name)`** — replaces `0xCB` in channel
  scope (block-walker bounded by next `0x40` / mixer-section markers).
- **`set_channel_color(path, iid, color: {r, g, b, a?})`** — RGBA
  components in `[0, 255]`. Replaces `0x80` u32 RGBA-packed event.
- **`set_channel_routing(path, iid, target_insert: int (-1..127))`** —
  routes channel to mixer insert; `-1` = unrouted (default to master).
  Replaces `0x16` u8 (signed int8) event.

### Mixer inserts (index 0-based, 0=Master)
- **`set_insert_name(path, index, name)`** — replaces `0xCC` blob.
- **`set_insert_color(path, index, color)`** — replaces `0x95` u32.

### Patterns (iid 1-based!)
- **`set_pattern_name(path, iid, name)`** — replaces `0xC1` blob.
- **`set_pattern_color(path, iid, color)`** — replaces `0x96` u32.
- **`clone_pattern(path, source_iid, name?)`** — duplicates pattern's
  full event subtree (notes, controllers, color, length, looped, name).
  New id = `max(existing) + 1`. Default name `"Pattern N copy"`.

### Pattern notes (Epic 5 / F2.1, opcode `0xE0`, 24-byte records)

Inverse of the parser's `decodeNotes`. Encoder writes byte-exact records
that re-parse identical. Verified end-to-end (FL load → piano roll →
File→Save → re-parse): all note fields survive a full FL round-trip.

- **`add_pattern_note(path, pattern_id, position, channel_iid, length, key, ...)`** —
  append one note to a pattern's `0xE0` blob. Required: `position` and
  `length` in PPQ ticks (read `metadata.ppq` from `describe`),
  `channel_iid` (cross-references `channels[].iid`), `key` (FL MIDI
  range `[0, 131]`; `60` = C5 in FL's numbering). Optional with
  defaults: `velocity` (100), `pan` (64=center), `fine_pitch` (120
  =neutral), `release` (64), `midi_channel` (0), `mod_x` (128),
  `mod_y` (128), `group` (0=ungrouped), `flags` (0). `slide: bool`
  toggles flag bit `0x08` (slide note). Existing notes in the pattern
  are preserved (encoder coalesces all notes into a single rewritten
  `0xE0`). `INVALID_ARGS` on out-of-range fields,
  `EVENT_NOT_FOUND` on unknown `pattern_id`.
- **`set_pattern_notes(path, pattern_id, notes)`** — replace every
  note on the pattern with the given list. `notes` is a JSON array of
  note objects (same fields as `add_pattern_note`). Empty array clears
  every `0xE0` event from the pattern's scope. Useful when the LLM
  wants to write a melody fresh rather than incrementally append.
- **`remove_pattern_note(path, pattern_id, index)`** — drop the note
  at the given 0-based index in stream order. `INVALID_ARGS` on
  out-of-range index.

### Pattern controllers (Epic 5 / F2.2, opcode `0xDF`, 12-byte records)

Inverse of `decodeControllers`. Pattern-scoped keyframe-automation
points: each record carries `(position, channel, value, flags)`. Used
by FL's "event editor" view in the piano roll.

- **`add_pattern_controller(path, pattern_id, position, channel, value, flags?)`** —
  append one controller event. `position` in PPQ ticks (u32),
  `channel` is a u8 (0..255), `value` is a float32, `flags` is a u8
  (default 0). `INVALID_ARGS` on out-of-range or non-finite value.
- **`set_pattern_controllers(path, pattern_id, controllers)`** —
  replace every controller record on the pattern. `controllers` is a
  JSON array of `{position, channel, value, flags?}` objects. Empty
  array drops every `0xDF` event in the pattern's scope.
- **`remove_pattern_controller(path, pattern_id, index)`** — drop the
  controller at the given 0-based index in stream order.

### Pattern + channel creation (Epic 5 / F2.3)

Create brand-new empty patterns and channels for the LLM to populate
via `add_pattern_note` / `set_pattern_notes` / `set_channel_color`
etc. Both helpers insert just before the first `0x63` (arrangement
opener) and round-trip clean through serialize→parse.

- **`create_pattern(path, name?)`** — emits the minimum on-disk
  shape `[0x41 newId u16, 0xC1 name blob]`. Returns
  `{path, bytes_written, pattern_id}` where `pattern_id =
  max(existing) + 1` (never reused, so deletions leave gaps and the
  next created pattern continues from the high-water mark). FL fills
  in defaults for length / color / looped / notes / controllers on
  read. `name` defaults to empty string.
- **`create_channel(path, name?, kind?)`** — emits `[0x40 newIid
  u16, 0x15 kindByte u8, 0xCB name blob]`. `kind` defaults to
  `"sampler"` (FL's built-in sampler renders fine with no plugin
  attached); other accepted values are `"instrument"` (kindByte=2,
  also writes an empty `0xC9` plugin-internal-name slot),
  `"layer"` (3), `"automation"` (5). Returns `{path, bytes_written,
  channel_iid}` where `channel_iid = max(existing) + 1`. The legacy
  `header.n_channels` u16 is intentionally NOT bumped — modern FL
  always writes 0 there and derives the real count from the event
  stream.

Typical chain (LLM-friendly): `create_pattern("verse2")` →
`create_channel("BassSynth", "instrument")` →
`add_pattern_note(pattern_id=NEW, channel_iid=NEW, …)`.

### Native plugin params (Epic 5 / F2.4)

Patch a single parameter inside a native FL plugin's `0xD5` state
blob. v0.1 supports **Fruity Parametric EQ 2 only** — both name
variants (`Fruity Parametric EQ 2` and FL 25.2.4's lowercase
`Fruity parametric EQ 2`). Other native plugins reject with
`UNSUPPORTED_PLUGIN`. **VST plugins are out of scope** — their state
blobs embed session-internal data that drifts across same-value
saves; use the live MIDI-script path (`live_execute(set_plugin_param)`)
instead.

- **`set_native_plugin_param(path, scope, param, value, …)`** —
  byte-patches one parameter slot.
  - `scope`: `"channel"` (with `channel_iid`) OR `"mixer_slot"` (with
    `insert_index` + `slot_index`). Insert 0 is Master.
  - `param`: `"main_level"` (uint16 LE at byte 0x90) OR `"band"`
    (with `band: 1..7` + `field: "level" | "freq" | "width"`).
  - `value`: normalized `0.0..1.0`, stored as `round(v * 0xFFFF)`.
    Out-of-range or non-finite values are rejected with
    `INVALID_ARGS`.
  - `EVENT_NOT_FOUND` if the scope has no `0xD5` event.
  - `UNSUPPORTED_PLUGIN` if the plugin name isn't in the registry, or
    the blob size is outside `[0x92, 500]` bytes (handles FL
    25.2.4's 354-byte and older FL saves' 350-byte EQ 2 blobs;
    rejects unrelated blobs that happen to live in the same scope).

  Type/order band fields (uint8 enums 0..7) are NOT supported in
  v0.1 — a normalized 0..1 mapping is lossy. Future API may add an
  enum-value variant.

  **Generic param refs (other native plugins):** the same kind also
  accepts `param: "param"` with `param_index: int` for any plugin
  registered in `PLUGIN_PARAM_LAYOUTS`. The index maps to FL's
  `plugins.setParamValue(value, paramIndex, ...)` numbering.

  **Adding more native plugins:** see
  `flpdiff/docs/fl-format/plugin-layout-registry.md`. The
  `python/tools/re_harness/sweep_plugin_layout.py` auto-sweep tool
  RE's a plugin's layout in <1 min by driving FL via IPC. Top
  corpus-frequency native plugins pending: Fruity Reeverb 2 (225
  instances), Fruity Limiter (174), Maximus (88), Soundgoodizer (88),
  Fruity Balance (82), Fruity PanOMatic (70), Fruity Filter (60).

  **VSTs:** explicitly out of scope — their state blobs drift across
  same-value saves. Use `live_execute(kind="set_plugin_param")` for
  Serum, Sylenth1, Massive, FabFilter, etc. (D-54).

### Arrangements + tracks
- **`set_arrangement_name(path, id: int (default 0), name)`** — replaces `0xF1` blob.
- **`set_track_name(path, arrangement: int (default 0), track: int, name)`** —
  replaces `0xEF` after the Nth `0xEE` track-data blob.
- **`set_track_color(path, arrangement, track, color)`** — patches
  bytes 4-7 of the 70-byte `0xEE` track-data blob in place; preserves
  all other track-data bytes (iid, icon, enabled, height, locked, +
  trailing motion/press/etc).

### Playlist clips
- **`add_clip(path, arrangement, kind: "pattern"|"channel", ref_id, track_index, position_ticks, length_ticks)`** —
  appends a 60-byte FL 21+ clip record to the LAST `0xE9` blob in the
  arrangement (preserves all existing record bytes verbatim).
  Reserved bytes in the new record zero (FL tolerates).
- **`remove_clip(path, arrangement, track_index, position_ticks?, ref_id?, kind?)`** —
  drop all matching clips. `track_index` required; optional fields
  narrow the match. Throws `EVENT_NOT_FOUND` if no match.
- **`move_clip(path, arrangement, track_index, position_ticks?, ref_id?, kind?, to_track_index?, to_position_ticks?)`** —
  patches matching records' position and/or track in place. At least
  one of `to_track_index` / `to_position_ticks` required.

### Atomic Ableton-style reorganize (playlist-only)
- **`reorganize_project(path, arrangement?, add_family_separators?, dry_run?)`** —
  one-shot deterministic cleanup of an arrangement's playlist tracks.
  **Never touches channels, mixer inserts, or patterns** — those carry
  intentional engineering (channel→insert routing, parallel chains,
  sample-name-as-source-info, pattern reuse semantics) that an
  automated tool would destroy.

  Algorithm: classify each clip by its referenced channel (or
  pattern's dominant channel) via regex on name + sample_path, MIDI-
  pitch fallback for unnamed plugins. Lay tracks out in fixed family
  order — Drums (hard) → Drums (soft) → Bass → Lead → Pad → FX →
  Vocal → Other — sorted by min(channel iid) within each family.
  Insert empty `[Family]` separator tracks between blocks. Move every
  clip to its lane's target track via `move_clip` (bulk-deduped per
  source-track + ref). Set track name + palette color. **Tracks within
  the generated layout are always renamed to match the new content** —
  preserving an old user name on a track that now carries different
  content would be misleading. Tracks beyond the layout range stay
  default-named.

  Args:
  - `arrangement` (default 0) — which arrangement to reorganize
  - `add_family_separators` (default true) — emit `[Drums]`,
    `[Bass]`, … empty tracks between family blocks
  - `dry_run` (default false) — return plan without writing

  Result includes `mutations_applied` + the full plan (`tracks[]`
  with name/rgb/grouped per row + `clipMoves[]` listing source/target
  tracks).

  ~400 ms per call regardless of project size; verified 85/85 on the
  full local corpus (FLPs from 60 KB to 9.9 MB, 1 to 122 channels,
  0 to 693 clips). Compared against an LLM-driven equivalent: 100×
  faster, deterministic, $0 / call.

  **v3 (2026-05-07)** adds automation→target nesting: automation
  channels whose target is a regular channel (decoded from opcode
  `0xE3`, RemoteController) become `grouped=true` children of their
  target's track. The automation lane inherits the target's family
  classification, so e.g. a `"Sytrus Pad - Filter 3 - Cutoff freq"`
  auto whose target is a bass channel lands in the `[Bass]` block
  (not `[Pad]`, despite the auto's own name). Mixer-slot
  automations (target encoded as a slot-pair, not a channel iid)
  stay as standalone rows in their own family — RE'ing the slot-
  pair encoding is deferred. Verified visually in FL on
  `test_track_color=…;automations=link_3.flp` (fold-arrow next to
  the auto track in Playlist sidebar) and byte-level on
  `NewStuff.flp` (1.2 MB, 144 channels, 19 linkable autos: 173
  tracks, 18 grouped, 5 family separators, 34 ms).

---

# Examples

## Read-only introspection

```python
live_execute(kind="describe")
# → {ok: true, kind: "describe", result: {metadata: {...}, channels: [...], ...}}

offline_execute(kind="list_clips", args={"path": "/Users/me/proj.flp", "arrangement": 0})
# → {ok: true, kind: "list_clips", result: [
#     {position_ticks: 0, length_ticks: 384, track_index: 0, kind: "pattern", ref_id: 1, group: 0, flags: 64},
#     ...
#   ]}
```

## Mutation with snapshot

```python
r = offline_execute(kind="set_channel_color",
                    args={"path": "/p.flp", "iid": 0, "color": {"r": 255, "g": 100, "b": 50}})
# → {ok: true, kind: "set_channel_color", result: {path: "/p.flp", bytes_written: 47602,
#                                                   snapshot_id: "p/20260503T180438-f8ed8e"}}

# Later, roll it back:
live_execute(kind="restore_snapshot", args={"snapshot_id": "p/20260503T180438-f8ed8e"})
```

## Reorganize-project (Ableton-style, fully offline)

```python
PATH = "/Users/me/messy_project.flp"

offline_execute(kind="set_arrangement_name", args={"path": PATH, "id": 0, "name": "Verse-A"})

for track, name, color in [
    (0, "DRUMS", {"r": 220, "g": 50,  "b": 47}),
    (1, "BASS",  {"r": 181, "g": 137, "b": 0}),
    (2, "KEYS",  {"r": 38,  "g": 139, "b": 210}),
    (3, "LEAD",  {"r": 211, "g": 54,  "b": 130}),
]:
    offline_execute(kind="set_track_name",  args={"path": PATH, "arrangement": 0, "track": track, "name": name})
    offline_execute(kind="set_track_color", args={"path": PATH, "arrangement": 0, "track": track, "color": color})
```

See `docs/examples/03-reorganize-project.md` for the full demo prompt.
