"""IAC Driver pre-flight + best-effort auto-enable (Phase 1.5).

The FL Studio MIDI script communicates over a virtual MIDI bus exposed
by macOS's Audio MIDI Setup app — specifically the **IAC Driver**. If
the IAC bus is offline, FL never loads the MIDI script and every
``live_execute`` call hangs.

This module:

* :func:`check_iac_status` — reads ``system_profiler
  SPMIDIAccessoryDataType -json`` and reports whether the IAC Driver is
  present and online. Pure read — no side effects.
* :func:`enable_iac_via_ui_scripting` — AppleScript that opens Audio
  MIDI Setup, double-clicks the IAC row, and toggles ``Device is
  online``. Best-effort — UI scripting breaks across macOS versions.

Detection is reliable; auto-enable is not. Callers should always fall
back to surfacing :data:`IAC_DRIVER_OFFLINE_ERROR` with the AMS
deeplink so the user can flip the toggle by hand.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum

from ..logging_setup import get_logger

IAC_DRIVER_OFFLINE_ERROR = "IAC_DRIVER_OFFLINE"
SYSTEM_PROFILER = "system_profiler"
OSASCRIPT = "osascript"
SYSTEM_PROFILER_DATA_TYPE = "SPMIDIAccessoryDataType"
SYSTEM_PROFILER_TIMEOUT_S = 10.0
OSASCRIPT_TIMEOUT_S = 20.0

_LOG = get_logger("installer.iac")


class IacState(StrEnum):
    """High-level IAC status values."""

    ONLINE = "online"  # IAC Driver present and online — ready to use
    OFFLINE = "offline"  # IAC Driver present but disabled
    NOT_INSTALLED = "not_installed"  # no IAC Driver entry at all
    UNKNOWN = "unknown"  # detection failed (system_profiler missing, parse error, etc.)


@dataclass(frozen=True)
class IacStatus:
    state: IacState
    detail: str = ""
    raw: dict | None = None

    @property
    def ok(self) -> bool:
        return self.state == IacState.ONLINE

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "ok": self.ok,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def _run_system_profiler() -> str:
    if shutil.which(SYSTEM_PROFILER) is None:
        raise FileNotFoundError(SYSTEM_PROFILER)
    proc = subprocess.run(
        [SYSTEM_PROFILER, SYSTEM_PROFILER_DATA_TYPE, "-json"],
        capture_output=True,
        text=True,
        timeout=SYSTEM_PROFILER_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"system_profiler exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def _extract_iac_entries(report: dict) -> list[dict]:
    """Walk the system_profiler JSON to find IAC Driver entries.

    The schema is roughly::

        {
          "SPMIDIAccessoryDataType": [
            {"_name": "...", ..., "_items": [
                {"_name": "IAC Driver", "online_state": "midi_state_online", ...}
            ]}
          ]
        }

    The exact shape varies across macOS versions, so the walk is
    tolerant: any nested dict whose ``_name`` matches IAC Driver
    counts.
    """
    found: list[dict] = []
    todo: list = [report]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            name = str(node.get("_name", "")).lower()
            if "iac driver" in name or name == "iac":
                found.append(node)
            todo.extend(node.values())
        elif isinstance(node, list):
            todo.extend(node)
    return found


def _entry_is_online(entry: dict) -> bool:
    """Return True if the entry's online_state field reports online."""
    state = str(entry.get("online_state", "")).lower()
    if "online" in state and "offline" not in state:
        return True
    legacy = str(entry.get("midi_state", "")).lower()
    return legacy == "online"


def check_iac_status() -> IacStatus:
    """Return the current IAC Driver state. Read-only, ~80ms."""
    try:
        raw = _run_system_profiler()
    except FileNotFoundError:
        return IacStatus(IacState.UNKNOWN, "system_profiler binary not found")
    except subprocess.TimeoutExpired:
        return IacStatus(IacState.UNKNOWN, "system_profiler timed out")
    except Exception as exc:
        return IacStatus(IacState.UNKNOWN, f"system_profiler error: {exc}")

    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        return IacStatus(IacState.UNKNOWN, f"system_profiler returned non-JSON: {exc}")

    entries = _extract_iac_entries(report)
    if not entries:
        return IacStatus(IacState.NOT_INSTALLED, "no IAC Driver entry in MIDI report")

    online = any(_entry_is_online(e) for e in entries)
    if online:
        return IacStatus(IacState.ONLINE, "", {"entries": entries})
    return IacStatus(IacState.OFFLINE, "IAC Driver present but disabled", {"entries": entries})


# --------------------------------------------------------------------------- #
# Auto-enable via UI scripting (best-effort)
# --------------------------------------------------------------------------- #


_ENABLE_IAC_APPLESCRIPT = """\
tell application "Audio MIDI Setup" to activate
delay 0.5
tell application "System Events"
    tell process "Audio MIDI Setup"
        -- Open MIDI Studio window (Window menu)
        try
            click menu item "Show MIDI Studio" of menu "Window" of menu bar 1
        on error
            -- already open or differently named in this OS version
        end try
        delay 0.5
        -- Activate the IAC Driver row + open its Inspector
        try
            tell window 1
                tell scroll area 1
                    tell list 1
                        select (UI element 1 whose value of attribute "AXTitle" contains "IAC")
                    end tell
                end tell
            end tell
        on error
            -- Older layout: some versions render IAC as a button in a toolbar
        end try
        delay 0.3
        -- Toggle "Device is online" via the Edit menu shortcut, if present.
        try
            click menu item "Show Info" of menu "View" of menu bar 1
        end try
        delay 0.3
        -- Final toggle: cmd-shift-O is the historic "online toggle" hotkey.
        try
            keystroke "o" using {command down, shift down}
        end try
    end tell
end tell
"""


def _run_osascript(script: str) -> tuple[int, str, str]:
    if shutil.which(OSASCRIPT) is None:
        raise FileNotFoundError(OSASCRIPT)
    proc = subprocess.run(
        [OSASCRIPT, "-e", script],
        capture_output=True,
        text=True,
        timeout=OSASCRIPT_TIMEOUT_S,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def enable_iac_via_ui_scripting() -> IacStatus:
    """Attempt to flip IAC online by driving Audio MIDI Setup via AppleScript.

    Best-effort. Returns the post-attempt :class:`IacStatus` — caller
    should still check ``.ok`` and surface :data:`IAC_DRIVER_OFFLINE_ERROR`
    if it's still offline.
    """
    try:
        rc, _stdout, stderr = _run_osascript(_ENABLE_IAC_APPLESCRIPT)
    except FileNotFoundError:
        _LOG.warning("osascript not found — cannot auto-enable IAC")
        return IacStatus(IacState.UNKNOWN, "osascript binary not found")
    except subprocess.TimeoutExpired:
        return IacStatus(IacState.UNKNOWN, "osascript timed out")

    if rc != 0:
        _LOG.warning(
            "IAC auto-enable AppleScript failed",
            extra={"rc": rc, "stderr_preview": stderr[:200]},
        )

    return check_iac_status()


def ensure_iac_online(*, attempt_enable: bool = True) -> IacStatus:
    """Convenience: report status and (optionally) try to auto-enable if offline."""
    status = check_iac_status()
    if status.ok or status.state == IacState.NOT_INSTALLED:
        return status
    if not attempt_enable:
        return status
    if status.state != IacState.OFFLINE:
        return status
    _LOG.info("IAC Driver offline — attempting UI-scripting auto-enable")
    return enable_iac_via_ui_scripting()
