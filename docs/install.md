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

### a. The bundled MIDI script must be in FL's Hardware dir + wired up

The auto-installer (or `live_execute(kind="install_script")`) does:

1. **Discovers every FL Studio user-data dir** matching
   `~/Documents/Image-Line/FL Studio*/` — handles both the modern
   shared layout (single `FL Studio/` dir for all 2024+ versions) and
   legacy per-version layouts (`FL Studio 20/`, `FL Studio 21/`,
   `FL Studio 2024/`, …). Installs into every one detected.
2. Copies `device_flstudio_mcp.py` into
   `<each>/Settings/Hardware/flstudio-mcp/` (subfolder — FL only scans
   `Hardware/<subdir>/device_*.py`, never `.py` files at the
   Hardware/ root).
3. Pre-creates the IPC runtime dirs
   (`flpdiff-harness/runtime/{inbox,outbox,processed}`) — required
   because FL's sandboxed Python can't create them itself.
4. Drops a `.version.json` sidecar so future installs are idempotent.

To install into one specific version only, set
`FLSTUDIO_MCP_HARDWARE_DIR` to that exact `Hardware/` path before
launching the server, or pass
`live_execute(kind="install_script", args={"hardware_dir": "/path/..."})`.

**You still have to wire the script inside FL once:**

1. Open **Options → MIDI Settings** (`F10`).
2. Click **Update MIDI scripts** (bottom of dialog) — this rescans the
   Hardware/ tree and adds `flstudio-mcp` to the Controller type
   dropdown. *(If FL was launched before the install, this step is
   required even after restart.)*
3. In the **Input** list, highlight **`IAC Driver Bus 1`**.
4. Click **Enable**.
5. Open the **Controller type** dropdown → under **Scripts**, pick
   **`flstudio-mcp`** (matches the `# name=flstudio-mcp` header at the
   top of the bundled script).
6. Optionally set **Port** to any number (1 is fine).
7. Close the dialog.

**Verify** in FL: View → Script output. You should see:

```
[flstudio-mcp] started, polling /Users/.../flstudio-mcp/runtime/inbox
```

If that line appears, MCP can talk to FL. Confirm end-to-end in one call:

```
live_execute(kind="verify_setup")
```

Returns `ok: true` when every link is wired (IAC online + script
installed + FL running + IPC handshake succeeds). On failure each step
reports its own `ok` + actionable detail so you can find which link is
broken without trawling logs.

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
| Server hangs on `live_execute(kind="describe")` | FL closed, or script not loaded, or IAC input not enabled | Click Update MIDI scripts; verify Section 3.a steps 2–6 |
| FL Script output doesn't show `[flstudio-mcp] started ...` | Controller type not set to `flstudio-mcp`, or IAC input not enabled | Re-do Section 3.a steps 3–5 |
| `flstudio-mcp` missing from Controller type dropdown | FL hasn't rescanned Hardware/ since install | Click **Update MIDI scripts** in MIDI Settings |
| Auto-enable IAC silently no-ops | Accessibility permission missing | System Settings → Privacy & Security → Accessibility → enable for terminal/IDE |
