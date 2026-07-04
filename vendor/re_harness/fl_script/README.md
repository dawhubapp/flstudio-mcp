# flpdiff RE harness — FL Studio side

The Python script FL Studio runs to receive commands from the orchestrator. Live-verified against **FL 25.2.4 Producer Edition**.

See `docs/fl-scripting-notes.md` for the sandbox map and the full set of confirmed APIs.

## Install (macOS, FL Studio 25)

```bash
# 1. Symlink the script into FL's user Hardware Scripts folder.
mkdir -p "$HOME/Documents/Image-Line/FL Studio/Settings/Hardware/flpdiff-harness"
ln -sf "$PWD/tools/re_harness/fl_script/device_flpdiff_harness.py" \
  "$HOME/Documents/Image-Line/FL Studio/Settings/Hardware/flpdiff-harness/"

# 2. Pre-create the runtime IPC dirs. FL's sandboxed Python cannot
#    create fresh subdirs; it can only write inside existing ones.
mkdir -p "$HOME/Documents/Image-Line/FL Studio/Settings/Hardware/flpdiff-harness/runtime"/{inbox,outbox,processed}
```

Then:

3. **Enable IAC Driver** (one-time):
   - `/Applications/Utilities/Audio MIDI Setup.app`
   - Window → Show MIDI Studio
   - Double-click **IAC Driver** → tick **"Device is online"** → Apply

4. **Wire it into FL**:
   - Launch FL Studio
   - Options → MIDI Settings
   - In the **Input** list, highlight **IAC Driver Bus 1**, click **Enable**, set Port (e.g. 1)
   - **Controller type**: `flpdiff-harness`
   - Close the dialog

5. **Verify**: View → Script output. You should see:
   ```
   [flpdiff-harness] started, polling /Users/you/Documents/Image-Line/FL Studio/Settings/Hardware/flpdiff-harness/runtime/inbox
   ```

## Sanity check (handshake)

```bash
.venv/bin/python -c "
from tools.re_harness.ipc import Command, default_inbox
inbox = default_inbox()
inbox.write_command(Command(id='smoke', kind='noop'))
print(inbox.wait_for_result('smoke', timeout=10))
"
```

Expected: `Result(id='smoke', status='ok', ...)`.

## One-shot end-to-end cycle (zero clicks)

```bash
.venv/bin/python -m tools.re_harness.cycle \
  --base tests/corpus/re_base/fl25/base_empty.flp \
  --set-tempo 145.0 \
  --auto
```

With `--auto`, the orchestrator opens the scratch copy in FL, sends `set_tempo`, clicks File → Save via accessibility, then prints the binary diff between base and saved-scratch.

`--reload-script` adds a "click Reload script" step up front — handy when iterating on handlers.

## Handler status (live-verified on FL 25.2.4)

| Command      | Status | Backing API |
|--------------|--------|-------------|
| `noop`       | ✅ OK  | none — handshake. |
| `describe`   | ✅ OK  | `mixer.getCurrentTempo`, `general.getProjectTitle`, `channels.channelCount`, `patterns.patternCount`, `mixer.trackCount`, `ui.getVersion`. |
| `get_tempo`  | ✅ OK  | `mixer.getCurrentTempo()` / 1000. |
| `set_tempo`  | ✅ OK  | `mixer.setCurrentTempo(bpm * 1000)`. Verified: BPM display updates, readback matches, file saves change on disk. |
| `save`       | ⚠️ — defer to `save_via_menu` | Script-side `transport.globalTransport(FPT_Save, 1)` returns ok but **does not flush**. Orchestrator drives save via AppleScript menu click instead. |
| `set_channel_volume` / `set_channel_pan` / `set_channel_name` | ❌ not implemented | Planned. |
| `set_insert_volume` / `set_insert_name` | ❌ not implemented | Planned. |
| `add_pattern_note` / `set_time_signature` / `open` | ❌ not implemented | Planned. |

## Sandbox quick reference

FL 25.2.4's embedded Python **cannot**:

- `os.remove` / `os.unlink`
- `os.replace` (rename)
- `os.makedirs` under fresh `~/Documents/<x>/` or anywhere in `~/Library/Application Support/`
- Access `__file__` (not set by FL's loader)
- Flush saves via `transport.globalTransport(FPT_Save)`

It **can**:

- `open(path, "w")` + `f.write(...)` freely (creates + writes)
- `os.makedirs` inside FL's own Hardware tree
- Read with `os.path.exists`, `os.listdir`, etc.
- `print(...)` to Script output

See `docs/fl-scripting-notes.md` for detail, including the macOS-specific `autodrive.py` helpers.

## Development loop

```bash
# Edit handlers in the repo
vim tools/re_harness/fl_script/device_flpdiff_harness.py

# Reload the script + re-run the cycle in one go
.venv/bin/python -m tools.re_harness.cycle \
  --base tests/corpus/re_base/fl25/base_empty.flp \
  --set-tempo 145.0 \
  --reload-script
```

The symlink means your edits are picked up the moment FL reloads.

## Gotchas

- **Avoid 3.10+ syntax** in `device_flpdiff_harness.py` (no `match`, no `x: int | None` in function signatures). FL's embedded Python is older than the rest of the project.
- **`print(...)`** is your only in-FL logging channel. Use `traceback.format_exc()` on exception — bare `str(exc)` loses info on C-level NULL failures.
- **`OnIdle` cadence is best-effort**. We throttle to 50ms; if a handler feels slow, look at the FL-side API call, not the IPC.
- **IAC Driver is mandatory** — FL won't initialize the script without a MIDI input attached.
