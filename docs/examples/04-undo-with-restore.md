# Demo 4 — Auto-snapshot + undo via restore_snapshot

> **Status:** RUNNABLE TODAY (Phase 2.1 + 2.2 + 2.3 shipped). Capture
> a real Claude Desktop transcript and paste responses below. Closes
> Phase 2.5.2.

## Why this demo matters

Snapshots are the safety net that lets the producer let the AI run
wild. Every mutation auto-captures the FLP first, so any change is
reversible. This demo proves the loop works in plain English without
the user ever needing to know what a "snapshot_id" is.

---

## Setup

1. Open FL with a project that has at least one channel + one
   pattern. Save it once so `flp_path` resolves.
2. `live_execute(kind="verify_setup")` → `ok: true`.
3. Note current tempo + project state for later comparison.

---

## The prompt sequence

### Prompt 1 — make a sweeping change

> **You:** Crank the tempo up to 175 and rename channel 0 to
> "STAB" — I want to see if a faster tempo helps the punch land.

Expected: agent calls `set_tempo(bpm=175)` then `set_channel_name
(iid=0, name="STAB")`. Each returns its own snapshot_id.

```
<paste response here>
```

### Prompt 2 — rethink

> **You:** Actually no, 175 feels too frantic. And "STAB" is wrong —
> the original name was better. Undo both.

Expected: agent recognizes "undo" intent, finds the right snapshot
(either via context or by reading `snapshots://recent`), calls
`restore_snapshot(snapshot_id=<pre-tempo>)`. Note: a single restore
of the *first* snapshot rolls back BOTH changes since FL re-reads the
file from disk. Agent should explain this rather than running two
restores.

Caveat for the demo: FL keeps the in-memory state until you reload
the project — `restore_snapshot` writes bytes to disk but FL's open
session doesn't auto-refresh. Agent should either:
- Tell user "I restored the file; close + reopen the project in FL to
  see the changes", OR
- Drive `cmd-Q` + relaunch via the same restart trick the test
  harness uses (out of scope for v0.5; mention as future polish).

```
<paste response here>
```

### Prompt 3 — verify

> **You:** Show me what's currently saved on disk.

Expected: agent reads `snapshots://recent` and explains the snapshot
chain (which mutations happened, what snapshot represents the current
disk state).

```
<paste response here>
```

---

## Acceptance criteria

- [ ] Both prompt-1 mutations succeeded with `snapshot_id` in the
      result envelope.
- [ ] `snapshots://recent` lists at least 2 fresh entries with
      `kind` matching the mutations (`set_tempo`, `set_channel_name`).
- [ ] `restore_snapshot` returned `ok: true` and wrote the
      pre-prompt-1 bytes back to the FLP on disk (verify by file
      mtime + sha256).
- [ ] Agent's natural-language reply explains the FL-still-shows-
      mutated-state caveat clearly so the user isn't confused.
- [ ] No `FILE_LOCKED_BY_FL` / `FL_DIALOG_BLOCKING` errors during
      restore (should pass since FL doesn't keep file handle open
      between accesses).

---

## Edge cases worth capturing

If the user has multiple snapshots and asks "undo", the agent should
either:
- Restore the most-recent pre-mutation snapshot by default, OR
- Show the snapshot list + ask which one

Capture whichever flow Claude picks naturally — both are valid.

If `restore_snapshot` runs while FL has the file open + dirty
(unsaved mutations the user made manually), the lsof-based check in
`SnapshotStore` may or may not catch it depending on whether FL keeps
a file handle open. If it does catch it, the error envelope should
include the `Close the project in FL` hint. Worth noting in the
transcript if the demo encounters it.

---

## Capture procedure

1. Open Claude Desktop, FL with a saved project, verify_setup green.
2. Run prompts 1-3 in order, copy each tool-call envelope.
3. After prompt 2's restore: `cd ~/Library/Application\ Support/
   flstudio-mcp/snapshots/<project>/` and `ls -la` to confirm the
   sidecar JSONs match what `snapshots://recent` reported.
4. Take FL screenshots before + after each change, save under
   `mcp/docs/examples/img/04-undo-{step}.png`.
5. Commit as `04-undo-with-restore-transcript.md`.

---

## Skill packaging

Trigger phrases: "undo that", "roll back", "I changed my mind, go
back", "revert to before X". The skill body teaches the agent to:
1. Read `snapshots://recent` first
2. Identify the right pre-mutation snapshot from context
3. Call `restore_snapshot`
4. Always explain the FL-reload caveat to the user

Standalone skill name candidate: `fl-studio:undo-via-snapshot`.
