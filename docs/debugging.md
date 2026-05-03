# Debugging flstudio-mcp

## MCP Inspector

The official MCP Inspector gives you a browser UI to call tools and resources
directly, see request/response payloads, and stream server stderr — much
faster than driving the server through Claude Desktop while iterating.

Requires Node 18+.

> All commands assume you're in the `mcp/` repo root. Replace `.` with an
> absolute path if running from elsewhere.

### Launch

uvx-from-path (rebuilds on every launch — slow but isolated):

```sh
npx @modelcontextprotocol/inspector uvx --from . flstudio-mcp
```

uv run (uses local `.venv` — fastest dev loop, edits picked up immediately):

```sh
npx @modelcontextprotocol/inspector uv run flstudio-mcp
```

The Inspector opens at `http://localhost:5173`.

### What to test

| Tool call | Needs FL running? | Purpose |
|-----------|-------------------|---------|
| `live_execute` `kind: list_apis` | no | Sanity check — server up, kinds enumerated |
| `live_execute` `kind: check_iac` | no | Verify IAC Driver detection |
| `live_execute` `kind: install_script` | no | Re-run MIDI script installer (covers all FL versions) |
| `live_execute` `kind: enable_iac` | no | Best-effort UI-scripting flip |
| `live_execute` `kind: verify_setup` | partial | End-to-end chain check; reports IAC + script + FL + IPC. Run this first when something's wrong. |
| `live_execute` `kind: describe` | yes | Project state from running FL (path resolved via window-title fallback on FL 2025) |
| `live_execute` `kind: get_tempo` | yes | Tempo readback |
| `live_execute` `kind: set_tempo` | yes | Mutation — `args: {bpm: 145}`. Auto-snapshots first. |
| `live_execute` `kind: set_channel_name` | yes | `args: {iid: 0, name: "Kick"}`. iid is **0-based**. |
| `live_execute` `kind: set_insert_volume` | yes | `args: {idx: 1, value: 0.7}`. idx **0 = Master**. |
| `live_execute` `kind: set_pattern_name` | yes | `args: {iid: 1, name: "Verse"}`. iid is **1-based** (FL pattern numbering). |
| `live_execute` `kind: set_time_signature` | yes | `args: {num: 7, beat: 8}` |
| `live_execute` `kind: set_plugin_param` | yes | `args: {index: 0, param: 0, value: 0.5, scope: "channel"}` |
| `live_execute` `kind: set_mixer_eq` | yes | `args: {idx: 1, band: 0, gain: 0.0}`. band 0=low, 1=mid, 2=high. |
| `live_execute` `kind: save` | yes | Triggers FL save (no auto-snapshot — save IS the write). |
| `live_execute` `kind: get_plugin_info` | yes | `args: {index: 0, scope: "channel"}` |
| `live_execute` `kind: restore_snapshot` | partial | `args: {snapshot_id: "..."}`. Requires FL to release file (close project). Use `snapshots://recent` to list ids. |

The `logs://recent` resource returns the last 50 structured log entries —
useful for seeing the full lifecycle of a tool call (begin / ok / error).

The `snapshots://recent` resource returns the last 100 FLP snapshots
(newest first) with `snapshot_id`, `project_slug`, `original_path`,
`created_at`, `sha256`, `size_bytes`, `kind`, `command_id`. Snapshots
live under `~/Library/Application Support/flstudio-mcp/snapshots/`.

### Pass env vars

```sh
npx @modelcontextprotocol/inspector \
  -e FLSTUDIO_MCP_LOG_LEVEL=DEBUG \
  -e FLSTUDIO_MCP_NO_AUTO_INSTALL=1 \
  uvx --from . flstudio-mcp
```

| Var | Effect |
|-----|--------|
| `FLSTUDIO_MCP_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`) |
| `FLSTUDIO_MCP_NO_AUTO_INSTALL` | Skip MIDI script auto-install at boot |
| `FLSTUDIO_MCP_HARDWARE_DIR` | Override FL Hardware dir (test isolation) |
| `FLSTUDIO_MCP_TELEMETRY` | `1`/`true` to enable backend (no-op until v1.x) |

## Tail server logs

Logs go to `~/Library/Logs/flstudio-mcp/server.log` (rotating JSON, 1 MiB
× 5 backups). Tail with `jq` for readability:

```sh
tail -f ~/Library/Logs/flstudio-mcp/server.log | jq .
```

Filter by `log_id` to follow one tool call:

```sh
tail -f ~/Library/Logs/flstudio-mcp/server.log \
  | jq 'select(.log_id == "abc123def456")'
```

## Run the test suite

```sh
uv run pytest -ra
```

88 unit tests, no FL needed. Integration tests are gated on
`FLSTUDIO_MCP_INTEGRATION=1` — only meaningful on the dev machine where
FL Studio is installed.

## Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ModuleNotFoundError: re_harness` | stale install, pre-1.1.5 layout | `uvx --reinstall --from <path> flstudio-mcp` |
| Inspector hangs on `describe` | FL closed or MIDI script not loaded | Open FL → Options → MIDI Settings → Refresh |
| `IAC_DRIVER_OFFLINE` | IAC bus disabled | See `install.md` §3.b |
| Stale tool descriptions in Inspector | UI cached | Refresh browser tab |
