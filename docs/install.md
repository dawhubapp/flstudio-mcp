# flstudio-mcp — install guide

> **Platform:** macOS only. FL Studio 25.x. Python 3.11+.

## 1. Install the server

```sh
uvx --from git+https://github.com/<org>/flstudio-mcp flstudio-mcp --version
```

(Org TBD — see Open Question 1 in `MCP-SPEC.md`.)

The first non-`--version` invocation auto-installs the bundled MIDI script
into FL Studio's Hardware directory and prints a one-line stderr notice.
Disable with `FLSTUDIO_MCP_NO_AUTO_INSTALL=1`.

## 2. Wire it into your MCP client

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "flstudio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<org>/flstudio-mcp", "flstudio-mcp"]
    }
  }
}
```

Restart Claude Desktop. The MCP panel should list `live_execute`.

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "flstudio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<org>/flstudio-mcp", "flstudio-mcp"]
    }
  }
}
```

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.flstudio]
command = "uvx"
args = ["--from", "git+https://github.com/<org>/flstudio-mcp", "flstudio-mcp"]
```

## 3. Required macOS state

flstudio-mcp depends on three pieces of host state. The server checks the
first two on boot and surfaces clear errors via `live_execute` for the
third.

### a. The bundled MIDI script must be in FL's Hardware dir

Auto-installed on server start. To re-run manually:

```
live_execute(kind="install_script")
```

After installation reload the device inside FL Studio:
**Options → MIDI Settings → click the Refresh button**.

### b. The IAC Driver must be online

FL Studio's MIDI script communicates over a virtual MIDI bus exposed by
macOS's **Audio MIDI Setup** app — specifically the **IAC Driver**. If
it's offline, FL never loads the script and every `live_execute` call
hangs.

Check via:

```
live_execute(kind="check_iac")
```

If it returns `state: "offline"`, attempt auto-enable:

```
live_execute(kind="enable_iac")
```

Auto-enable drives Audio MIDI Setup via UI scripting, so it requires
**System Settings → Privacy & Security → Accessibility** permission for
your terminal / IDE / Claude Desktop binary. The first attempt will
typically fail with an Accessibility prompt — grant the permission and
retry.

If auto-enable still fails, flip it manually:

1. Open **Audio MIDI Setup** (`open -a "Audio MIDI Setup"`).
2. Choose **Window → Show MIDI Studio** if the MIDI Studio window isn't
   already visible.
3. Double-click the **IAC Driver** row.
4. Tick **Device is online**.
5. Close the inspector. Status flips immediately — no restart needed.

> _Screenshot:_ TODO — add `docs/img/iac-driver-online.png` showing the
> Audio MIDI Setup window with the "Device is online" checkbox ticked.

### c. FL Studio must be running for live calls

`live_execute` calls (other than `install_script`, `check_iac`,
`enable_iac`, `list_apis`) require an FL Studio instance with an open
project. The auto-install only puts the MIDI script in place; FL still
needs to be launched and have the device enabled at least once via
**Options → MIDI Settings**.

If FL is closed, calls return `FL_NOT_RUNNING` (Phase 2.4).

## 4. Verification

```
live_execute(kind="list_apis")
```

Should return the supported `kind` enum. With FL open on a project,
`live_execute(kind="describe")` returns project state.

Logs go to `~/Library/Logs/flstudio-mcp/server.log` (rotating JSON).
The `logs://recent` MCP resource returns the last 50 entries.

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `IAC_DRIVER_OFFLINE` | IAC bus disabled in Audio MIDI Setup | Section 3.b |
| `HARDWARE_DIR_MISSING` install action | FL Studio 25.x not installed | Install FL Studio |
| Server hangs on `live_execute(kind="describe")` | FL closed, or MIDI script not reloaded | Open FL → Options → MIDI Settings → Refresh |
| Auto-enable IAC silently no-ops | Accessibility permission missing | System Settings → Privacy & Security → Accessibility → enable for terminal/IDE |
