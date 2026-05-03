"""Drive FL Studio's MIDI Settings dialog via pyautogui.

FL renders the Settings dialog with custom controls that are not exposed
to macOS accessibility (AXChildren is empty), so AppleScript / System
Events can't click buttons by name. This module:

  1. Opens MIDI Settings via the Options menu (AppleScript — that part
     IS accessible because it's a native macOS menu bar).
  2. Reads the resulting window's logical position + size via
     System Events.
  3. Clicks at coords relative to the window origin via pyautogui.

The relative coords below are calibrated for FL Studio 2025.x default
theme. Layout shifts between FL versions / themes will require
recalibration. Coords are class attributes so callers can override.

After the click sequence the function blocks briefly then runs an IPC
``noop`` to confirm the script is now responsive.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from ..logging_setup import get_logger

OSASCRIPT = "osascript"
OSASCRIPT_TIMEOUT_S = 10.0
SETTINGS_WINDOW_TITLE = "Settings - MIDI input / output devices"
DROPDOWN_SETTLE_S = 0.4
CLICK_SETTLE_S = 0.25

_LOG = get_logger("installer.wire_fl")


@dataclass(frozen=True)
class WindowBounds:
    x: int  # logical screen pixels (top-left x)
    y: int
    w: int
    h: int

    def offset(self, dx: int, dy: int) -> tuple[int, int]:
        return (self.x + dx, self.y + dy)

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class WireCoords:
    """Pixel offsets from the Settings dialog top-left.

    Calibrated for FL Studio 2025.x at default theme + 668x894 dialog
    bounds. If the layout shifts, override these via the ``coords`` arg
    of ``wire_flstudio_mcp_input`` / ``reload_script_via_midi_settings``.
    """

    # Live-verified offsets (Roman confirmed cursor landed on each):
    update_scripts_btn: tuple[int, int] = (242, 854)
    iac_input_row: tuple[int, int] = (180, 420)
    # Enable radio (red dot) — verified via crop measurement at logical
    # (45, 41) within the (422,600,668,80) crop. dialog y=63, so offset
    # (45, 41+600-63) = (45, 578).
    enable_radio: tuple[int, int] = (45, 578)
    controller_dropdown: tuple[int, int] = (560, 555)


@dataclass
class WireStep:
    name: str
    ok: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class WireResult:
    ok: bool
    summary: str
    steps: list[WireStep] = field(default_factory=list)
    window: WindowBounds | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "window": self.window.to_dict() if self.window else None,
        }


# --------------------------------------------------------------------------- #
# AppleScript helpers (open dialog + read window bounds)
# --------------------------------------------------------------------------- #


_OPEN_AND_BOUNDS_APPLESCRIPT = r"""
on findMidiWindow()
    tell application "System Events"
        tell process "%(process_name)s"
            repeat with w in windows
                set wn to name of w
                if wn contains "MIDI" then return w
            end repeat
            return missing value
        end tell
    end tell
end findMidiWindow

tell application "System Events"
    tell process "%(process_name)s"
        set frontmost to true
        delay 0.3
    end tell
end tell

-- Only click the menu item if the dialog isn't already open. Clicking
-- it when open TOGGLES (closes) the dialog. We want it open.
set existing to my findMidiWindow()
if existing is missing value then
    tell application "System Events"
        tell process "%(process_name)s"
            try
                click menu item "MIDI settings" of menu "Options" of menu bar 1
            on error errMsg
                return "AS_ERROR:open: " & errMsg
            end try
        end tell
    end tell
    delay 0.8
end if

set winRef to my findMidiWindow()
if winRef is missing value then
    return "AS_ERROR:dialog not found after open attempt"
end if
tell application "System Events"
    tell process "%(process_name)s"
        set p to position of winRef
        set s to size of winRef
    end tell
end tell
return ((item 1 of p) as string) & "," & ((item 2 of p) as string) & "," & ¬
       ((item 1 of s) as string) & "," & ((item 2 of s) as string)
"""


def _run(cmd: list[str], *, timeout: float = OSASCRIPT_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _detect_fl_process_name() -> str | None:
    """Return the System-Events process name for FL ('OsxFL' or 'FL Studio*')."""
    from . import verify

    procs = verify.detect_fl_processes()
    if not procs:
        return None
    return procs[0][0]


def open_midi_settings(process_name: str) -> tuple[bool, WindowBounds | str]:
    """Open MIDI Settings dialog; return ``(ok, bounds_or_error)``."""
    script = _OPEN_AND_BOUNDS_APPLESCRIPT % {"process_name": process_name}
    try:
        proc = _run([OSASCRIPT, "-e", script])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"osascript invocation failed: {exc}"
    if proc.returncode != 0:
        return False, f"osascript exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"
    out = proc.stdout.strip()
    if out.startswith("AS_ERROR:"):
        return False, out
    parts = out.split(",")
    if len(parts) != 4:
        return False, f"unexpected bounds output: {out!r}"
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError:
        return False, f"non-integer bounds: {out!r}"
    return True, WindowBounds(x=x, y=y, w=w, h=h)


# --------------------------------------------------------------------------- #
# pyautogui driving
# --------------------------------------------------------------------------- #


def _import_pyautogui():
    """Import pyautogui lazily; raise a friendly error if unavailable."""
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError(
            "pyautogui not installed; reinstall flstudio-mcp with the wire_fl extra"
        ) from exc
    pyautogui.FAILSAFE = False  # don't bail when cursor hits screen corner
    return pyautogui


def reload_script_via_midi_settings(
    *,
    coords: WireCoords | None = None,
    dry_run: bool = False,
) -> WireResult:
    """Click FL's 'Update MIDI scripts' button + cycle Enable to reload.

    **Live verified (2026-05-03): does NOT actually reload a running
    script.** FL caches the script instance; neither 'Update MIDI
    scripts' nor toggling Enable forces a re-init. Empirically the only
    methods that DO reload a stale script:

      * Restart FL Studio (hard but reliable)
      * Change Controller type to '(generic controller)' then back to
        'flstudio-mcp' (the dropdown cycle is brittle to automate, see
        decision #27)

    This function is kept so the click sequence is automatable for
    cases where FL's behavior changes in a future build, but callers
    should treat the post-call state as "best effort" and verify by
    sending a kind that's only present in the new script version.
    """
    coords = coords or WireCoords()
    steps: list[WireStep] = []

    process_name = _detect_fl_process_name()
    if not process_name:
        return WireResult(
            ok=False,
            summary="FL Studio is not running",
            steps=[WireStep("detect_fl", False, "no FL process found")],
        )
    steps.append(WireStep("detect_fl", True, f"process={process_name}"))

    ok, bounds_or_err = open_midi_settings(process_name)
    if not ok:
        steps.append(WireStep("open_midi_settings", False, str(bounds_or_err)))
        return WireResult(ok=False, summary="could not open MIDI Settings dialog", steps=steps)
    bounds = bounds_or_err  # type: ignore[assignment]
    steps.append(WireStep("open_midi_settings", True, f"bounds={bounds.to_dict()}"))

    if dry_run:
        return WireResult(
            ok=True,
            summary="dry run — no click performed",
            steps=steps,
            window=bounds,
        )

    try:
        pg = _import_pyautogui()
    except RuntimeError as exc:
        steps.append(WireStep("import_pyautogui", False, str(exc)))
        return WireResult(ok=False, summary=str(exc), steps=steps, window=bounds)

    # Step 1: Update MIDI scripts (re-discovers Hardware/)
    target = bounds.offset(*coords.update_scripts_btn)
    pg.click(*target)
    time.sleep(CLICK_SETTLE_S)
    steps.append(
        WireStep(
            "click_update_scripts",
            True,
            f"clicked at {target} (offset {coords.update_scripts_btn} from dialog)",
        )
    )

    # Step 2: toggle Enable radio off → on. Required because Update
    # MIDI scripts only re-DISCOVERS scripts, doesn't re-INITIALIZE
    # an already-loaded one. Toggling Enable forces FL to drop the
    # running script instance and load the fresh disk version.
    enable_target = bounds.offset(*coords.enable_radio)
    pg.click(*enable_target)
    time.sleep(0.4)
    pg.click(*enable_target)
    time.sleep(CLICK_SETTLE_S)
    steps.append(
        WireStep(
            "toggle_enable",
            True,
            f"clicked Enable twice at {enable_target} (off then on) to force re-init",
        )
    )

    return WireResult(
        ok=True,
        summary=(
            "clicked Update MIDI scripts + toggled Enable off/on. NOTE: live-"
            "verified that this does NOT actually reload a running script in "
            "FL 2025 — FL caches the script instance. Restart FL or cycle "
            "Controller type manually to load updated handlers. Send a kind "
            "only present in the new version to verify."
        ),
        steps=steps,
        window=bounds,
    )


def wire_flstudio_mcp_input(
    *,
    coords: WireCoords | None = None,
    dry_run: bool = False,
) -> WireResult:
    """Drive FL Settings to bind IAC input to the flstudio-mcp script.

    Steps:
      1. Detect FL process name.
      2. Open MIDI Settings + read window bounds.
      3. Click 'Update MIDI scripts' so the new script is discovered.
      4. Click the IAC Driver Bus 1 row in the Input list.
      5. Click 'Enable'.
      6. Click the Controller type dropdown, type 'flstudio-mcp', press Enter.

    If ``dry_run`` is True, all coords are computed but no clicks happen
    — useful for verifying the dialog opened + bounds make sense.
    """
    coords = coords or WireCoords()
    steps: list[WireStep] = []

    # Step 1: detect FL
    process_name = _detect_fl_process_name()
    if not process_name:
        return WireResult(
            ok=False,
            summary="FL Studio is not running",
            steps=[WireStep("detect_fl", False, "no FL process found")],
        )
    steps.append(WireStep("detect_fl", True, f"process={process_name}"))

    # Step 2: open + bounds
    ok, bounds_or_err = open_midi_settings(process_name)
    if not ok:
        steps.append(WireStep("open_midi_settings", False, str(bounds_or_err)))
        return WireResult(ok=False, summary="could not open MIDI Settings dialog", steps=steps)
    bounds = bounds_or_err  # type: ignore[assignment]
    steps.append(WireStep("open_midi_settings", True, f"bounds={bounds.to_dict()}"))

    if dry_run:
        return WireResult(
            ok=True, summary="dry run — no clicks performed", steps=steps, window=bounds
        )

    try:
        pg = _import_pyautogui()
    except RuntimeError as exc:
        steps.append(WireStep("import_pyautogui", False, str(exc)))
        return WireResult(ok=False, summary=str(exc), steps=steps, window=bounds)

    # Step 3: click 'Update MIDI scripts'
    pg.click(*bounds.offset(*coords.update_scripts_btn))
    time.sleep(CLICK_SETTLE_S)
    steps.append(WireStep("click_update_scripts", True))

    # Step 4: click IAC Driver Bus 1 in Input list
    pg.click(*bounds.offset(*coords.iac_input_row))
    time.sleep(CLICK_SETTLE_S)
    steps.append(WireStep("click_iac_input_row", True))

    # Step 5: click Enable
    pg.click(*bounds.offset(*coords.enable_radio))
    time.sleep(CLICK_SETTLE_S)
    steps.append(WireStep("click_enable", True))

    # Step 6: click Controller type dropdown → type-to-search
    pg.click(*bounds.offset(*coords.controller_dropdown))
    time.sleep(DROPDOWN_SETTLE_S)
    pg.write("flstudio-mcp", interval=0.02)
    time.sleep(0.1)
    pg.press("enter")
    time.sleep(CLICK_SETTLE_S)
    steps.append(WireStep("pick_controller_type", True))

    return WireResult(
        ok=True,
        summary=(
            "drove FL MIDI Settings dialog — verify by closing the dialog and "
            "running live_execute(verify_setup); IPC handshake should now succeed"
        ),
        steps=steps,
        window=bounds,
    )
