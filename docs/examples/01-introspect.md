# Demo 1 — Introspect a running FL Studio project

> **Setup:** flstudio-mcp wired into Claude Desktop (`docs/install.md`),
> FL Studio 2025 running with any project loaded, IAC + MIDI script
> verified via `live_execute(verify_setup)` returning `ok: true`.

This transcript walks Claude Desktop through a 3-prompt conversation
that exercises the read-only surface of `live_execute`. Replace the
`<paste actual response here>` blocks with what Claude Desktop returns
when you run it live, then commit.

---

## Prompt 1 — Discover what tools are available

> **You:** What MCP tools do you have for FL Studio? Show me what
> kinds of operations are supported.

Expected behavior: Claude calls `live_execute(kind="list_apis")` and
summarizes the supported `kind` enum.

```
<paste actual Claude response here>
```

---

## Prompt 2 — Verify everything is wired

> **You:** Before we start, run a setup check and confirm everything
> is wired correctly. Tell me which steps passed.

Expected behavior: Claude calls `live_execute(kind="verify_setup")`
and reports each step's `ok` status with the IPC handshake duration.

Expected JSON envelope (real values from your machine will differ):

```json
{
  "ok": true,
  "summary": "all checks passed — flstudio-mcp is wired up",
  "steps": [
    { "name": "iac_driver_online", "ok": true,  "detail": "CoreMIDI sees 1 destination(s) + 1 source(s); IAC plugin present" },
    { "name": "script_installed",  "ok": true,  "detail": "present in 1 install(s)" },
    { "name": "fl_studio_running", "ok": true,  "detail": "running: OsxFL (pid 61753)" },
    { "name": "ipc_handshake",     "ok": true,  "detail": "noop ok in 105.8ms" }
  ]
}
```

```
<paste actual Claude response here>
```

---

## Prompt 3 — Read project state

> **You:** What's the tempo of the project I have open right now? And
> how many channels and patterns does it have?

Expected behavior: Claude calls `live_execute(kind="describe")` (or
`get_tempo` then `describe`) and answers in plain language.

Expected JSON envelope from `describe`:

```json
{
  "ok": true,
  "kind": "describe",
  "result": {
    "flp_path": "/Users/<you>/.../some-project.flp",
    "project_title": "<your title>",
    "tempo_bpm": 130.0,
    "channel_count": 5,
    "pattern_count": 1,
    "insert_count": 105,
    "api_version": "FL Studio 2025"
  },
  "duration_ms": 104.0,
  "log_id": "<12-char hex>"
}
```

```
<paste actual Claude response here>
```

---

## Verification checklist for this transcript

- [ ] Each prompt returned a tool call (visible in Claude Desktop's
      "Tool use" disclosure).
- [ ] All `live_execute` envelopes have `ok: true`.
- [ ] `describe` `result.flp_path` matches the FLP you actually have
      open in FL.
- [ ] `tempo_bpm` matches FL's tempo display.
- [ ] `live_execute(verify_setup)` `ipc_handshake.duration_ms < 500`
      (sub-half-second confirms FL is responsive).

If any of those fail, run `live_execute(verify_setup)` first and read
the failing step's `detail` for diagnosis.

---

## How to capture this

1. Open Claude Desktop with the `flstudio` MCP server configured
   (see `docs/install.md` Section 2).
2. Open FL Studio 2025 with any project loaded.
3. Send the three prompts above in order.
4. For each prompt: expand the tool-call disclosure in Claude Desktop,
   copy the tool result JSON, paste into the matching `<paste actual
   Claude response here>` block above.
5. Also capture Claude's natural-language reply under each.
6. Commit this file as the Phase 1.6.2 deliverable.
