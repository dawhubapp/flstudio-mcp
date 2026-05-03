# Demo 2 — Rewrite a drum pattern

> **Status:** RUNNABLE TODAY (Phase 2.3 step-sequencer ops shipped).
> Capture a real Claude Desktop transcript and paste responses into
> the placeholder blocks below. Closes Phase 2.5.1.

## Why this demo matters

The bread-and-butter MCP-driven music-production loop: producer is
stuck on a beat, asks the AI to try variations, sees results live in
FL Studio, picks the one they like, keeps going. No clicking through
menus, no manual step entry — natural-language → working pattern in
seconds.

This demo proves the snapshot/mutation/rollback flow works end-to-end
for the common case (pattern editing) without piano-roll dependencies.

---

## Setup

1. Open FL with a project that has at least 4 channels in the rack
   named **Kick**, **Snare**, **Hat**, **Clap** (or similar — any
   drum sounds work; the agent infers role from name).
2. Verify wiring: `live_execute(kind="verify_setup")` → `ok: true`.
3. The active pattern should be empty (or one you don't mind
   overwriting). Run `clear_pattern_steps` for each channel first if
   needed.

---

## The prompt (paste into Claude Desktop / Cursor)

> **You:** Build me a basic four-on-the-floor house pattern in the
> active FL pattern. Kick on every beat, clap on 2 and 4, hats on
> off-beats, snare on 3. Use 16-step bars. Show me the grid you laid
> down, then ask me which channel I want to make busier.

Expected behavior:

1. `live_execute(kind="describe")` — confirm channel count + which
   channels are present.
2. `live_execute(kind="get_pattern_steps", args={"channel": 0,
   "count": 16})` for each candidate channel — figure out which is
   kick / snare / hat / clap by name (via describe channel names) or
   by reading existing patterns.
3. **For each role**, call `set_step` per active position:
   - Kick (channel 0): steps 0, 4, 8, 12 (every quarter)
   - Clap (channel ?): steps 4, 12 (beats 2 + 4)
   - Hat (channel ?): steps 2, 6, 10, 14 (off-beats)
   - Snare (channel ?): step 8 (beat 3)
4. `get_pattern_steps` per channel — read back the placed grid and
   render it as ASCII or table for the user.
5. Wait for user's "make X busier" instruction.

```
<paste actual Claude response from prompt 1 here>
```

---

## Follow-up prompts (capture these too)

### Prompt 2 — busier hats

> **You:** Make the hats sixteenths instead of eighths.

Expected: `clear_pattern_steps(channel=hat_iid)` then 16× `set_step`
for every step 0..15. Read back to confirm.

```
<paste response here>
```

### Prompt 3 — undo

> **You:** Actually, that's too dense. Roll it back to before you
> made it sixteenths.

Expected: agent calls `restore_snapshot` with the snapshot_id from
just before the busier-hats edit. (Snapshots accumulated in
`snapshots://recent` — agent should know which one.)

```
<paste response here>
```

---

## Acceptance criteria

- [ ] Each `set_step` returned `ok: true` with a `snapshot_id`.
- [ ] Final `get_pattern_steps` for each role-channel returned
      exactly the expected step indices (kick=[0,4,8,12], etc.).
- [ ] Visual check in FL channel rack: lit dots match the readback.
- [ ] Agent successfully restored the pre-busier-hats snapshot when
      asked to undo (verify the hats grid matches the eighths
      pattern again, not sixteenths).
- [ ] No `INVALID_ARGS`, `IPC_TIMEOUT`, or `SNAPSHOT_FAILED` errors
      surfaced anywhere in the chain.

---

## Capture procedure

1. Open Claude Desktop with `flstudio` MCP wired (per
   `docs/install.md`).
2. Open FL with a 4-channel drum project.
3. Send each prompt in order, waiting for the agent's reply +
   verification before sending the next.
4. After each agent reply, expand the tool-call disclosure and copy
   the JSON envelope into the matching `<paste response here>` block.
5. Take a FL screenshot of the channel rack after each step (kick →
   plus clap → plus hat → plus snare → busier hats → restore). Save
   under `mcp/docs/examples/img/02-rewrite-{step}.png`.
6. Commit as `02-rewrite-pattern-transcript.md` next to this file.

---

## Skill packaging

Strong candidate for `fl-studio:write-drum-pattern` skill later.
Trigger phrases: "give me a drum pattern", "build me a four-on-the-
floor", "lay down a hip-hop beat", "make a breakbeat in the active
pattern". The chain is repeatable — the agent only needs to know the
genre + channel-name → role mapping.
