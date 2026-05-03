"""End-to-end FL Studio setup verifier.

Drives FL Studio via AppleScript / IPC to check that every link in the
chain is wired:

  1. IAC Driver is online (CoreMIDI sees endpoints).
  2. Bundled MIDI script is installed at the right Hardware path.
  3. FL Studio process is running.
  4. FL's Script output window contains ``[flstudio-mcp] started``
     (proves FL loaded the script + IAC input is bound to it).
  5. ``noop`` IPC handshake round-trips successfully.

Each step's outcome is structured so the caller can show the user
exactly which link is broken. Steps fail fast — if FL isn't running
there's no point checking script output — but the result preserves
the partial chain.

Used by ``live_execute(kind="verify_setup")``. Reuses the existing
``autodrive.activate_fl_studio`` / ``ensure_script_output_open``
helpers from re_harness; adds a script-output reader of its own.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from ..logging_setup import get_logger
from . import iac as iac_installer
from . import midi_script as midi_installer

OSASCRIPT = "osascript"
PGREP = "pgrep"
FL_PROCESS_NAME = "FL Studio"
SCRIPT_STARTED_MARKER = "[flstudio-mcp] started"
NOOP_TIMEOUT_S = 3.0
SUBPROCESS_TIMEOUT_S = 10.0

_LOG = get_logger("installer.verify")


@dataclass(frozen=True)
class VerifyStep:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "ok": self.ok, "detail": self.detail}
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    steps: list[VerifyStep] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def check_iac() -> VerifyStep:
    status = iac_installer.check_iac_status()
    return VerifyStep(
        name="iac_driver_online",
        ok=status.ok,
        detail=status.detail or status.state.value,
        data=status.to_dict(),
    )


def check_script_installed() -> VerifyStep:
    """At least one FL install must have the script at the right path."""
    dirs = midi_installer.discover_fl_hardware_dirs()
    if not dirs:
        return VerifyStep(
            name="script_installed",
            ok=False,
            detail="no FL Studio user-data dirs found under ~/Documents/Image-Line/",
        )
    targets = [hw / midi_installer.SCRIPT_SUBDIR / midi_installer.SCRIPT_FILENAME for hw in dirs]
    present = [str(p) for p in targets if p.exists()]
    if not present:
        return VerifyStep(
            name="script_installed",
            ok=False,
            detail="bundled script not found at any FL Studio Hardware/flstudio-mcp/ path",
            data={"checked": [str(p) for p in targets]},
        )
    return VerifyStep(
        name="script_installed",
        ok=True,
        detail=f"present in {len(present)} install(s)",
        data={"present": present},
    )


def _run(cmd: list[str], *, timeout: float = SUBPROCESS_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def check_fl_running() -> VerifyStep:
    try:
        proc = _run([PGREP, "-x", FL_PROCESS_NAME])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return VerifyStep(
            name="fl_studio_running",
            ok=False,
            detail=f"could not run pgrep: {exc}",
        )
    if proc.returncode == 0 and proc.stdout.strip():
        pids = [int(x) for x in proc.stdout.split() if x.strip().isdigit()]
        return VerifyStep(
            name="fl_studio_running",
            ok=True,
            detail=f"running (pid {pids[0]})" if pids else "running",
            data={"pids": pids},
        )
    return VerifyStep(
        name="fl_studio_running",
        ok=False,
        detail="FL Studio process not found — open it before verifying",
    )


_READ_SCRIPT_OUTPUT_APPLESCRIPT = r"""
tell application "FL Studio" to activate
delay 0.2
tell application "System Events"
    tell process "FL Studio"
        try
            -- Open Script output window if not visible.
            if not (exists window "Script output") then
                click menu item "Script output" of menu "View" of menu bar 1
                delay 0.4
            end if
            -- Read everything from the text area inside it.
            set t to value of text area 1 of scroll area 1 of window "Script output"
            return t
        on error errMsg
            return "AS_ERROR:" & errMsg
        end try
    end tell
end tell
"""


def read_fl_script_output(timeout: float = SUBPROCESS_TIMEOUT_S) -> tuple[bool, str]:
    """Return ``(ok, text_or_error)`` from FL's Script output window."""
    try:
        proc = _run([OSASCRIPT, "-e", _READ_SCRIPT_OUTPUT_APPLESCRIPT], timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"osascript invocation failed: {exc}"
    if proc.returncode != 0:
        return False, f"osascript exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"
    text = proc.stdout
    if text.startswith("AS_ERROR:"):
        return False, text
    return True, text


def check_script_output_loaded() -> VerifyStep:
    ok, text = read_fl_script_output()
    if not ok:
        return VerifyStep(
            name="script_output_loaded",
            ok=False,
            detail=f"could not read FL Script output: {text[:200]}",
        )
    if SCRIPT_STARTED_MARKER not in text:
        last_lines = "\n".join(text.splitlines()[-10:])
        return VerifyStep(
            name="script_output_loaded",
            ok=False,
            detail=(
                f"FL Script output does not contain {SCRIPT_STARTED_MARKER!r}. "
                "Either the script hasn't been wired in MIDI Settings, or it "
                "raised at startup. Last 10 lines:\n" + last_lines
            ),
            data={"last_lines": last_lines},
        )
    return VerifyStep(
        name="script_output_loaded",
        ok=True,
        detail=f"FL Script output contains {SCRIPT_STARTED_MARKER!r}",
    )


def check_ipc_handshake(runtime) -> VerifyStep:
    """Send `noop` and wait briefly. Slow path == script not polling inbox."""
    started = time.time()
    try:
        result = runtime.noop(timeout_s=NOOP_TIMEOUT_S)
    except TimeoutError:
        return VerifyStep(
            name="ipc_handshake",
            ok=False,
            detail=(
                f"noop timed out after {NOOP_TIMEOUT_S}s — script may not be "
                "polling the inbox. Check FL → Options → MIDI Settings → "
                "Controller type for IAC input is 'flstudio-mcp'."
            ),
        )
    except Exception as exc:
        return VerifyStep(
            name="ipc_handshake",
            ok=False,
            detail=f"noop raised {type(exc).__name__}: {exc}",
        )
    elapsed_ms = round((time.time() - started) * 1000.0, 1)
    if getattr(result, "status", None) != "ok":
        return VerifyStep(
            name="ipc_handshake",
            ok=False,
            detail=f"noop returned status={result.status!r} detail={result.detail[:200]!r}",
        )
    return VerifyStep(
        name="ipc_handshake",
        ok=True,
        detail=f"noop ok in {elapsed_ms}ms",
        data={"duration_ms": elapsed_ms},
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def verify_setup(runtime, *, skip_ui: bool = False) -> VerifyResult:
    """Run every verification step. Stops once one fails (chain depends).

    Parameters
    ----------
    runtime
        A LiveRuntime-shaped object with a ``noop()`` method. Pass a
        fake to keep tests off real FL.
    skip_ui
        If True, skip the AppleScript-driven Script output read. Useful
        when running headless (CI) or when accessibility perms aren't
        granted yet.
    """
    steps: list[VerifyStep] = []

    iac_step = check_iac()
    steps.append(iac_step)
    if not iac_step.ok:
        return VerifyResult(
            ok=False, steps=steps, summary="IAC Driver not online — see step detail"
        )

    install_step = check_script_installed()
    steps.append(install_step)
    if not install_step.ok:
        return VerifyResult(
            ok=False,
            steps=steps,
            summary="MIDI script not installed — run live_execute(install_script)",
        )

    running_step = check_fl_running()
    steps.append(running_step)
    if not running_step.ok:
        return VerifyResult(ok=False, steps=steps, summary="FL Studio is not running")

    if not skip_ui:
        loaded_step = check_script_output_loaded()
        steps.append(loaded_step)
        if not loaded_step.ok:
            return VerifyResult(
                ok=False,
                steps=steps,
                summary=(
                    "FL Studio is running but hasn't loaded the script — wire it "
                    "via Options → MIDI Settings → Update MIDI scripts → set "
                    "Controller type to 'flstudio-mcp'"
                ),
            )

    handshake_step = check_ipc_handshake(runtime)
    steps.append(handshake_step)
    if not handshake_step.ok:
        return VerifyResult(ok=False, steps=steps, summary="IPC handshake failed — see step detail")

    return VerifyResult(
        ok=True, steps=steps, summary="all checks passed — flstudio-mcp is wired up"
    )
