"""IAC Driver pre-flight + best-effort auto-enable (Phase 1.5).

The FL Studio MIDI script communicates over a virtual MIDI bus exposed
by macOS's Audio MIDI Setup app — specifically the **IAC Driver**. If
the IAC bus is offline, FL never loads the MIDI script and every
``live_execute`` call hangs.

Detection (best-effort, no extra deps):

1. **CoreMIDI count** — ``MIDIGetNumberOfDestinations`` /
   ``MIDIGetNumberOfSources`` via ctypes. Reliable across macOS
   versions. Counts > 0 indicate at least one MIDI endpoint is
   currently online.
2. **IAC plugin file presence** —
   ``/System/Library/Extensions/AppleMIDIIACDriver.plugin`` exists on
   every modern macOS install. Distinguishes NOT_INSTALLED (rare) from
   OFFLINE.
3. **Legacy fallback** — ``system_profiler SPMIDIAccessoryDataType
   -json``. Modern macOS returns an empty array even when IAC is
   present + online, so this is only useful on older systems and as a
   last resort.

Caveat: the count probe can't distinguish IAC ports from hardware MIDI
endpoints. If a user has only a hardware MIDI device connected and IAC
is offline, this detector reports ONLINE (false positive). In practice
nearly every macOS without explicit hardware MIDI shows endpoints only
when IAC is online, so the heuristic is good enough for v0.1. CoreMIDI
name enumeration via ctypes / JXA was tried and segfaults on macOS 14;
revisit once a stable bridge exists.

Auto-enable drives Audio MIDI Setup via osascript — fragile across
macOS versions but worth attempting before falling back to the
``IAC_DRIVER_OFFLINE`` error + manual instructions.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..logging_setup import get_logger

IAC_DRIVER_OFFLINE_ERROR = "IAC_DRIVER_OFFLINE"
SYSTEM_PROFILER = "system_profiler"
OSASCRIPT = "osascript"
SYSTEM_PROFILER_DATA_TYPE = "SPMIDIAccessoryDataType"
SYSTEM_PROFILER_TIMEOUT_S = 10.0
OSASCRIPT_TIMEOUT_S = 20.0

# IAC Driver kernel extension — present on every modern macOS install.
IAC_PLUGIN_PATH = Path("/System/Library/Extensions/AppleMIDIIACDriver.plugin")

_LOG = get_logger("installer.iac")


class IacState(StrEnum):
    """High-level IAC status values."""

    ONLINE = "online"
    OFFLINE = "offline"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


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
# Primary detection: CoreMIDI endpoint count via ctypes
# --------------------------------------------------------------------------- #


def _coremidi_endpoint_counts() -> tuple[int, int] | None:
    """Return ``(destinations, sources)`` from CoreMIDI, or ``None`` on failure."""
    path = ctypes.util.find_library("CoreMIDI")
    if not path:
        return None
    try:
        lib = ctypes.cdll.LoadLibrary(path)
        lib.MIDIGetNumberOfDestinations.restype = ctypes.c_uint32
        lib.MIDIGetNumberOfSources.restype = ctypes.c_uint32
        return int(lib.MIDIGetNumberOfDestinations()), int(lib.MIDIGetNumberOfSources())
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Secondary detection: IAC plugin presence + legacy system_profiler
# --------------------------------------------------------------------------- #


def _iac_plugin_installed(plugin_path: Path | None = None) -> bool:
    return (plugin_path or IAC_PLUGIN_PATH).exists()


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
    state = str(entry.get("online_state", "")).lower()
    if "online" in state and "offline" not in state:
        return True
    return str(entry.get("midi_state", "")).lower() == "online"


def _system_profiler_status() -> IacStatus | None:
    """Legacy detection. Returns ``None`` when system_profiler reports nothing
    useful (modern macOS), so callers can fall through to other heuristics."""
    try:
        raw = _run_system_profiler()
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, OSError):
        return None
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        return None
    entries = _extract_iac_entries(report)
    if not entries:
        return None
    if any(_entry_is_online(e) for e in entries):
        return IacStatus(
            IacState.ONLINE, "system_profiler reports IAC online", {"entries": entries}
        )
    return IacStatus(IacState.OFFLINE, "system_profiler reports IAC offline", {"entries": entries})


# --------------------------------------------------------------------------- #
# Public detection
# --------------------------------------------------------------------------- #


def check_iac_status() -> IacStatus:
    """Return the current IAC Driver state.

    Combines three signals:
      1. CoreMIDI endpoint counts (most reliable cross-version probe).
      2. IAC plugin file presence.
      3. system_profiler (legacy / older macOS).

    See module docstring for the false-positive caveat (hardware MIDI
    devices are indistinguishable from IAC at the count level).
    """
    counts = _coremidi_endpoint_counts()
    plugin_installed = _iac_plugin_installed()

    if counts is not None:
        dest, src = counts
        any_endpoint = (dest + src) > 0
        raw = {"dest_count": dest, "src_count": src, "plugin_installed": plugin_installed}

        if any_endpoint and plugin_installed:
            return IacStatus(
                IacState.ONLINE,
                f"CoreMIDI sees {dest} destination(s) + {src} source(s); IAC plugin present",
                raw,
            )
        if any_endpoint and not plugin_installed:
            # MIDI endpoints exist but IAC plugin missing — rare; treat as not installed
            return IacStatus(
                IacState.NOT_INSTALLED,
                "MIDI endpoints exist but IAC plugin is missing",
                raw,
            )
        if not any_endpoint and plugin_installed:
            return IacStatus(
                IacState.OFFLINE,
                "IAC plugin installed but no MIDI endpoints active",
                raw,
            )
        return IacStatus(
            IacState.NOT_INSTALLED,
            "No MIDI endpoints and IAC plugin missing",
            raw,
        )

    fallback = _system_profiler_status()
    if fallback is not None:
        return fallback
    if plugin_installed:
        return IacStatus(
            IacState.UNKNOWN,
            "CoreMIDI not reachable; IAC plugin present, online state unknown",
        )
    return IacStatus(
        IacState.NOT_INSTALLED,
        "CoreMIDI not reachable and IAC plugin missing",
    )


# --------------------------------------------------------------------------- #
# Auto-enable via UI scripting (best-effort)
# --------------------------------------------------------------------------- #


_ENABLE_IAC_APPLESCRIPT = """\
tell application "Audio MIDI Setup" to activate
delay 0.5
tell application "System Events"
    tell process "Audio MIDI Setup"
        try
            click menu item "Show MIDI Studio" of menu "Window" of menu bar 1
        on error
        end try
        delay 0.5
        try
            tell window 1
                tell scroll area 1
                    tell list 1
                        select (UI element 1 whose value of attribute "AXTitle" contains "IAC")
                    end tell
                end tell
            end tell
        on error
        end try
        delay 0.3
        try
            click menu item "Show Info" of menu "View" of menu bar 1
        end try
        delay 0.3
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

    Best-effort. Returns the post-attempt :class:`IacStatus`.
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
