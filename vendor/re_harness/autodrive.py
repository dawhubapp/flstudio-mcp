"""macOS automation helpers for driving FL Studio from the outside.

Two things the cycle helper wants to do without human clicking:

1. **Reload the MIDI script** after we change handler code. FL's Script
   Output window has a "Reload script" button; AppleScript can target it
   by name via the System Events accessibility API (Accessibility perms
   are already required for this project, so no extra setup).

2. **Open a specific FLP file** in the running FL Studio. macOS's
   ``open -a`` command tells the running app to accept a file — the
   equivalent of File → Open, minus the dialog.

Intentionally small. If/when AppleScript targeting gets brittle (FL
renames the button, theme changes, whatever) the fix is an image-
recognition fallback via pyautogui — not a rewrite.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class AutodriveError(RuntimeError):
    """Raised when an automation step fails in a way worth surfacing."""


# --------------------------------------------------------------------------- #
# App discovery
# --------------------------------------------------------------------------- #

# FL on macOS is a Wine-bridged Windows app: the .app bundle is
# "FL Studio <year>.app" but the running process registers under the Wine
# launcher name "OsxFL" (FL 2025+). Older versions may surface as
# "FL Studio" via Apple Events. AppleScript activation via the bundle name
# (`tell application "FL Studio" to activate`) silently times out on the
# Wine bridge — that's D-47 in MCP-SPEC.md. Address the process by its
# real name through System Events instead.
#
# `open -a` (LaunchServices) DOES need the bundle name and that varies
# across versions ("FL Studio 2025.app", "FL Studio 2024.app", etc.) —
# auto-detect the latest installed bundle.

_APPLICATIONS = Path("/Applications")
_FL_APP_RE = re.compile(r"^FL Studio(?: (?P<version>\d+))?\.app$")

# Default process name on FL 2025+. Override via ``FLPDIFF_FL_PROCESS`` env
# var, or call ``resolve_fl_process_name()`` to auto-detect.
FL_PROCESS_NAME = "OsxFL"

_DETECT_PROCESS_APPLESCRIPT = r"""
tell application "System Events"
    set out to ""
    repeat with p in (every process whose name is "OsxFL" or name starts with "FL Studio")
        set out to (name of p)
        exit repeat
    end repeat
    return out
end tell
"""


@lru_cache(maxsize=1)
def resolve_fl_process_name() -> str:
    """Return the actual System Events process name for FL Studio.

    Order: ``FLPDIFF_FL_PROCESS`` env override → osascript detection
    (``OsxFL`` on FL 2025+, ``FL Studio*`` on older versions) → fall back
    to ``FL_PROCESS_NAME`` constant. Cached for the process lifetime.
    """
    override = os.environ.get("FLPDIFF_FL_PROCESS")
    if override:
        return override
    try:
        result = subprocess.run(
            ["osascript", "-e", _DETECT_PROCESS_APPLESCRIPT],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        detected = result.stdout.strip()
        if detected:
            return detected
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("FL process detection via osascript failed: %s", exc)
    return FL_PROCESS_NAME


def _detect_fl_app_bundle() -> str:
    """Return the most recent ``FL Studio *.app`` bundle name in /Applications.

    Prefers higher version numbers (2025 > 2024 > 21 > 20). Env override:
    ``FLPDIFF_FL_APP=`` to pin a specific bundle.
    """
    override = os.environ.get("FLPDIFF_FL_APP")
    if override:
        return override
    candidates: list[tuple[int, str]] = []
    if _APPLICATIONS.exists():
        for entry in _APPLICATIONS.iterdir():
            m = _FL_APP_RE.match(entry.name)
            if not m:
                continue
            # Version is lexicographic-safe with padding to 5 digits so "2025"
            # beats "21" as expected.
            version = int(m.group("version") or 0)
            candidates.append((version, entry.name))
    if not candidates:
        raise AutodriveError(
            "No FL Studio*.app found in /Applications. Set FLPDIFF_FL_APP env "
            "var to your bundle name if it's installed elsewhere."
        )
    candidates.sort(reverse=True)
    return candidates[0][1]


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #


def activate_fl_studio(timeout: float = 5.0) -> None:
    """Bring FL Studio to the foreground.

    Uses System Events ``set frontmost to true`` against the resolved
    process (``OsxFL`` on FL 2025+) — ``tell application "FL Studio" to
    activate`` silently hangs on the Wine bridge (D-47).
    """
    proc = resolve_fl_process_name()
    script = (
        f'tell application "System Events" to tell process "{proc}" '
        "to set frontmost to true"
    )
    subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def ensure_script_output_open(timeout: float = 5.0) -> None:
    """Make sure the 'Script output' window is visible.

    FL's `Reload script` button only exists when that window is open. The
    View menu item toggles it, so we check-then-click.
    """
    proc = resolve_fl_process_name()
    script = f"""
    tell application "System Events"
        tell process "{proc}"
            set frontmost to true
            delay 0.1
            -- If the window already exists, we're done.
            if exists window "Script output" then
                return "already_open"
            end if
            -- Otherwise toggle it via the View menu.
            click menu item "Script output" of menu "View" of menu bar 1
            delay 0.3
            if exists window "Script output" then
                return "opened"
            else
                return "error: Script output window still not present"
            end if
        end tell
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    logger.info("ensure_script_output_open: %s", result.stdout.strip())
    if result.returncode != 0:
        raise AutodriveError(
            f"osascript failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def reload_midi_script(timeout: float = 5.0) -> str:
    """Click FL's Script-output 'Reload script' button.

    Ensures the Script output window is visible first (otherwise the button
    doesn't exist in the accessibility tree). Returns the AppleScript
    status string — ``"clicked"`` on success, ``"error: <detail>"``
    otherwise.
    """
    ensure_script_output_open()
    proc = resolve_fl_process_name()
    script = f"""
    tell application "System Events"
        tell process "{proc}"
            try
                click button "Reload script" of window "Script output"
                return "clicked"
            on error errMsg
                return "error: " & errMsg
            end try
        end tell
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AutodriveError(f"osascript failed: {result.stderr.strip() or result.stdout.strip()}")
    out = result.stdout.strip()
    logger.info("reload_midi_script: %s", out)
    return out


def close_current_project(timeout: float = 10.0) -> str:
    """Close whatever project FL Studio has open right now.

    Sends File → Close via the accessibility menu, then an auto-Enter to
    dismiss any "save unsaved changes?" prompt with its default. Safe to
    call when no project is open — the menu item is greyed out and the
    click is a no-op.

    Used before each cycle run so FL starts from a known state (no stale
    in-memory project); otherwise ``open -a`` with a different scratch
    path may leave the prior project active and saves go to the wrong
    file.
    """
    proc = resolve_fl_process_name()
    script = f"""
    tell application "System Events"
        tell process "{proc}"
            set frontmost to true
            delay 0.2
            try
                click menu item "Close" of menu "File" of menu bar 1
            on error errMsg
                return "error: click Close — " & errMsg
            end try
            delay 0.4
            try
                -- Dismiss unsaved-changes prompt if it appeared. The
                -- "N" key is FL's "No, don't save" choice; Enter accepts
                -- the default (usually Save). We want "Don't save" for the
                -- close-before-open case — otherwise we'd re-save the
                -- stale state we're trying to get rid of.
                keystroke "n"
            end try
            return "clicked:File>Close"
        end tell
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AutodriveError(f"close_current_project failed: {result.stderr.strip()}")
    return result.stdout.strip()


def save_via_menu(*, dismiss_prompt: bool = True, timeout: float = 15.0) -> str:
    """Trigger Save through FL's File menu.

    The MIDI-script-side ``transport.globalTransport(FPT_Save, 1)`` returns
    "ok" but doesn't flush to disk on FL 25 (probably sandbox-related).
    Clicking File → Save via accessibility is robust because it doesn't
    depend on keyboard focus.

    FL sometimes shows a confirmation prompt on save (e.g., "Save a copy?"
    when the project came from an unusual path). ``dismiss_prompt=True``
    (default) sends Enter after a short delay to accept the default
    button. Set it False if you need to see what the prompt actually says.

    Returns the AppleScript status string.
    """
    proc = resolve_fl_process_name()
    script = f"""
    tell application "System Events"
        tell process "{proc}"
            set frontmost to true
            delay 0.4
            try
                click menu item "Save" of menu "File" of menu bar 1
            on error errMsg
                return "error: click Save — " & errMsg
            end try
            REPLACE_DISMISS
            return "clicked:File>Save"
        end tell
    end tell
    """
    dismiss = (
        r"""
            delay 0.6
            try
                -- Accept any popup with its default button (Return = default).
                keystroke return
            end try
        """
        if dismiss_prompt
        else ""
    )
    script = script.replace("REPLACE_DISMISS", dismiss)
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AutodriveError(f"save_via_menu failed: {result.stderr.strip()}")
    return result.stdout.strip()


def open_flp(path: Path, *, wait_seconds: float = 3.0) -> None:
    """Ask the running FL Studio to open ``path``.

    Uses macOS's ``open -a``. Assumes FL is already running; if not, it'll
    launch (cold start is slow — callers can pass bigger ``wait_seconds``).

    There's no synchronous "loaded" signal from FL, hence the sleep. For
    tighter feedback, follow this call with a ``describe`` round-trip.
    """
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    bundle = _detect_fl_app_bundle()
    result = subprocess.run(
        ["open", "-a", bundle, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AutodriveError(
            f"`open -a {bundle!r}` failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    time.sleep(wait_seconds)


def dismiss_modals(count: int = 3, *, delay: float = 0.4) -> int:
    """Send `count` Enter keystrokes to FL to dismiss generic modals.

    Real producer FLPs almost always trigger one or more "missing
    plugin / sample" dialogs on first load. Their default button is
    typically "OK" / "Continue", so ``keystroke return`` clears them.
    Stacked dialogs need multiple presses. Returns the number of
    dismiss attempts that didn't error.

    Does NOT dismiss PACE License Support fatal-error dialogs (those
    have a single "OK" button but FL's accessibility tree is already
    collapsed by the time they appear; the Enter goes to a frozen
    process). Those need a force-kill.
    """
    proc = resolve_fl_process_name()
    ok = 0
    for _ in range(count):
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "System Events" to tell process "{proc}" to set frontmost to true',
                ],
                check=False,
                capture_output=True,
                timeout=2.0,
            )
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to keystroke return'],
                check=False,
                capture_output=True,
                timeout=2.0,
            )
            ok += 1
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(delay)
    return ok


def restart_fl(*, flp_path: Path | None = None, wait_seconds: float = 18.0) -> None:
    """Force-kill FL (SIGKILL) and relaunch, optionally with `flp_path`.

    The most reliable IPC recovery — per D-32, FL's MIDI script
    hot-reload doesn't work on macOS; only a fresh process boot
    re-initializes the script's polling loop. Use as a last-resort
    fallback when `wait_for_clean_fl` fails to handshake after N
    dismiss attempts.

    If `flp_path` is given, FL launches with that project loaded.
    Otherwise FL boots into its default empty project.
    """
    procs = subprocess.run(
        ["pgrep", "-f", "OsxFL"], check=False, capture_output=True, text=True
    )
    pids = [p.strip() for p in procs.stdout.splitlines() if p.strip()]
    for pid in pids:
        try:
            subprocess.run(["kill", "-9", pid], check=False, capture_output=True, timeout=3.0)
        except (subprocess.SubprocessError, OSError):
            pass
    if pids:
        time.sleep(3.0)  # let macOS reap + accessibility clear
    if flp_path is not None:
        open_flp(flp_path, wait_seconds=wait_seconds)
    else:
        bundle = _detect_fl_app_bundle()
        subprocess.run(["open", "-a", bundle], check=False, capture_output=True, timeout=10.0)
        time.sleep(wait_seconds)


def wait_for_clean_fl(
    *,
    inbox=None,
    max_attempts: int = 8,
    dismiss_per_attempt: int = 2,
    retry_delay: float = 2.5,
    handshake_timeout: float = 3.0,
    restart_after: int = 0,
    restart_flp: Path | None = None,
) -> bool:
    """Poll FL's MIDI-script IPC; between failed attempts dismiss any
    pending modals via Enter keystrokes.

    Returns True once the IPC handshake succeeds, False if all
    `max_attempts` failed (FL still has a blocking dialog or the
    script genuinely isn't installed).

    Use BEFORE driving FL via IPC commands when opening real producer
    FLPs — those routinely have stacked missing-plugin / missing-sample
    dialogs that block the script's inbox-poll loop until dismissed.

    The `inbox` arg is the IPC inbox to ping (default:
    `re_harness.ipc.default_inbox()`); kept as a parameter so callers
    can inject a test double.
    """
    # Lazy import to avoid circular deps at module load.
    from .ipc import Command, default_inbox  # noqa: WPS433

    if inbox is None:
        inbox = default_inbox()

    for attempt in range(1, max_attempts + 1):
        cmd = Command(id=f"clean-fl-probe-{int(time.time() * 1000)}-{attempt}", kind="noop")
        inbox.write_command(cmd)
        try:
            inbox.wait_for_result(cmd.id, timeout=handshake_timeout)
            logger.info("wait_for_clean_fl: IPC live after %d attempt(s)", attempt)
            return True
        except TimeoutError:
            logger.info(
                "wait_for_clean_fl: attempt %d/%d failed; dismissing modals…",
                attempt,
                max_attempts,
            )
            dismiss_modals(count=dismiss_per_attempt, delay=0.4)
            # Last-resort recovery: force-restart FL after `restart_after`
            # consecutive dismiss-only failures. Per D-32 hot-reload of
            # the MIDI script doesn't work on macOS — only a fresh boot
            # re-initialises the polling loop.
            if restart_after > 0 and attempt == restart_after:
                logger.warning(
                    "wait_for_clean_fl: %d attempts failed via dismiss-only; "
                    "force-restarting FL%s",
                    attempt,
                    f" with {restart_flp}" if restart_flp else "",
                )
                try:
                    restart_fl(flp_path=restart_flp)
                except Exception as exc:  # noqa: BLE001
                    logger.error("restart_fl failed: %s", exc)
            time.sleep(retry_delay)
    logger.warning(
        "wait_for_clean_fl: %d attempts failed — FL likely has a sticky modal "
        "(PACE plugin license fatal error?) or the MIDI script isn't running",
        max_attempts,
    )
    return False


# --------------------------------------------------------------------------- #
# Window-bounds + screenshot helpers — used by e2e visual-acceptance gates.   #
# --------------------------------------------------------------------------- #

# AppleScript returns a list of ints on one line: "100, 200, 1600, 1000".
_FRONT_WINDOW_BOUNDS_APPLESCRIPT = """\
tell application "System Events" to tell process "{process}"
    set winPos to position of window 1
    set winSize to size of window 1
    return (item 1 of winPos) & ", " & (item 2 of winPos) & ", " & (item 1 of winSize) & ", " & (item 2 of winSize)
end tell
"""


def get_fl_window_bounds() -> tuple[int, int, int, int]:
    """Return ``(x, y, width, height)`` of FL's frontmost window.

    Uses System Events accessibility to read window position + size of
    the FL process. Raises :class:`AutodriveError` if FL is not running
    or no window is visible.
    """
    process = resolve_fl_process_name()
    try:
        result = subprocess.run(
            ["osascript", "-e", _FRONT_WINDOW_BOUNDS_APPLESCRIPT.format(process=process)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except subprocess.CalledProcessError as exc:
        raise AutodriveError(
            f"window bounds query failed for process {process!r}: {exc.stderr.strip()}"
        ) from exc
    raw = result.stdout.strip()
    try:
        parts = [int(p.strip()) for p in raw.split(",")]
    except ValueError as exc:
        raise AutodriveError(f"unexpected window bounds output: {raw!r}") from exc
    if len(parts) != 4:
        raise AutodriveError(f"expected 4 ints (x,y,w,h), got {raw!r}")
    return tuple(parts)  # type: ignore[return-value]


def screenshot_fl_window(
    out_path: Path,
    *,
    full_screen: bool = False,
    fallback_to_full_screen: bool = True,
) -> Path:
    """Capture FL's frontmost window to ``out_path`` as PNG.

    Uses macOS ``screencapture -x`` (silent: no shutter sound). When
    ``full_screen`` is True, captures the entire screen instead of just
    the FL window — useful when FL has multiple windows open or the
    target detail (playlist, channel rack) is on a secondary display.

    Window-bounds queries go through System Events, which requires
    the caller process to have macOS Accessibility permission. When
    AX is denied (error -1719), default behaviour is to fall back to
    a full-screen capture rather than fail — the screenshot is more
    visually noisy but still useful for downstream vision callers.
    Set ``fallback_to_full_screen=False`` to surface the AX error
    directly.

    Returns the same path on success. Raises :class:`AutodriveError`
    on failure.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    region: str | None = None
    if not full_screen:
        try:
            x, y, w, h = get_fl_window_bounds()
            region = f"{x},{y},{w},{h}"
        except AutodriveError:
            if not fallback_to_full_screen:
                raise
            logger.warning(
                "screenshot_fl_window: bounds query denied (Accessibility "
                "permission missing for this Python binary?), falling back "
                "to full-screen capture. Grant access via System Settings → "
                "Privacy & Security → Accessibility to get tight crops."
            )
    cmd = ["screencapture", "-x"]
    if region is not None:
        cmd += ["-R", region]
    cmd.append(str(out_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10.0)
    except subprocess.CalledProcessError as exc:
        raise AutodriveError(
            f"screencapture failed: {exc.stderr.decode('utf-8', errors='replace').strip()}"
        ) from exc
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise AutodriveError(f"screencapture produced no file at {out_path}")
    return out_path


__all__ = [
    "FL_PROCESS_NAME",
    "AutodriveError",
    "activate_fl_studio",
    "close_current_project",
    "dismiss_modals",
    "ensure_script_output_open",
    "get_fl_window_bounds",
    "open_flp",
    "reload_midi_script",
    "resolve_fl_process_name",
    "restart_fl",
    "save_via_menu",
    "screenshot_fl_window",
    "wait_for_clean_fl",
]
