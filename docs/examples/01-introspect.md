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
There's exactly one FL Studio tool: `flstudio:live_execute`, a single entry point with 8 supported "kinds". Here's the actual surface area:

**Inspection (read-only)**
- `describe` — project path, title, tempo, channel/pattern/insert counts, FL version
- `get_tempo` — current BPM
- `list_apis` — enumerates supported kinds

**Setup & diagnostics (macOS-specific)**
- `install_script` — re-runs the bundled FL MIDI script installer
- `check_iac` — reports IAC Driver state (online/offline/not_installed)
- `enable_iac` — UI-scripting attempt to flip IAC online
- `verify_setup` — end-to-end check (IAC + script + FL running + IPC handshake)
- `wire_input` — pyautogui-driven binding of IAC Bus 1 to the FL MIDI script

**Honest take, since you asked:** this is v0.1 and it shows. It's almost entirely plumbing — most of the surface is "is the IAC bridge wired up correctly?" The only creative read is project metadata and tempo. There's no transport control, no MIDI write, no pattern/channel manipulation, no mixer access, no plugin parameter control, no render/export. You can't actually *do* anything to a project, only ask FL what it currently looks like.

Given your interest in wrapping FL Studio as an MCP server for agentic DAW control, this is the seed of that idea, not the thing itself. The IAC + MIDI script foundation is the right plumbing choice (going through the documented MIDI scripting API beats hacking at the binary), but the agent-useful verbs — note input, transport, pattern editing, mixer routing, plugin automation — are all still to be built. If you wanted to ship the paid desktop app you've been thinking about, you'd basically be building on top of this scaffolding rather than extending a mostly-complete API.

Want me to actually call `describe` against your running FL instance to see what state it reports?
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
All green. Four steps, all passed:

1. **IAC driver online** — CoreMIDI sees 1 destination + 1 source, IAC plugin present
2. **Script installed** — `device_flstudio_mcp.py` found in your FL Studio Settings/Hardware folder
3. **FL Studio running** — OsxFL, pid 61753
4. **IPC handshake** — noop round-tripped in 124ms

Total verify took 3.5s, mostly the UI/AppleScript checks. The handshake itself is fast, so latency to FL won't be the bottleneck for whatever you build next.

Ready when you are — want me to `describe` the current project?
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
- **Tempo:** 130 BPM
- **Channels:** 5
- **Patterns:** 0

One thing worth flagging: pattern count is 0 and project title/path are both null, which means this is an unsaved, untouched new project — you've got 5 channels in the rack but haven't actually committed any notes to a pattern yet. If you were expecting to inspect a real session, you might have the wrong FL window focused, or just not opened the .flp you meant to.

For reference, you're on Producer Edition v25.2.4 (build 4960).
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
