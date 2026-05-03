# Debugging flstudio-mcp

## MCP Inspector

The official MCP Inspector gives you a browser UI to call tools and resources
directly, see request/response payloads, and stream server stderr — much
faster than driving the server through Claude Desktop while iterating.

Requires Node 18+.

### Launch

From anywhere (uvx pulls fresh build each time):

```sh
npx @modelcontextprotocol/inspector uvx --from /Users/pronskiy/projects/dawhub/mcp flstudio-mcp
```

From inside the repo (uses local `.venv`, edits picked up immediately):

```sh
cd /Users/pronskiy/projects/dawhub/mcp
npx @modelcontextprotocol/inspector uv run flstudio-mcp
```

The Inspector opens at `http://localhost:5173`.

### What to test

| Tool call | Needs FL running? | Purpose |
|-----------|-------------------|---------|
| `live_execute` `kind: list_apis` | no | Sanity check — server up, kinds enumerated |
| `live_execute` `kind: check_iac` | no | Verify IAC Driver detection |
| `live_execute` `kind: install_script` | no | Re-run MIDI script installer |
| `live_execute` `kind: enable_iac` | no | Best-effort UI-scripting flip |
| `live_execute` `kind: describe` | yes | Project state from running FL |
| `live_execute` `kind: get_tempo` | yes | Tempo readback |

The `logs://recent` resource returns the last 50 structured log entries —
useful for seeing the full lifecycle of a tool call (begin / ok / error).

### Pass env vars

```sh
npx @modelcontextprotocol/inspector \
  -e FLSTUDIO_MCP_LOG_LEVEL=DEBUG \
  -e FLSTUDIO_MCP_NO_AUTO_INSTALL=1 \
  uvx --from /Users/pronskiy/projects/dawhub/mcp flstudio-mcp
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
cd /Users/pronskiy/projects/dawhub/mcp
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
