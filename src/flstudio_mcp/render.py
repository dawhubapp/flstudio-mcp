"""Render an FLP to WAV with FL Studio (F7.2).

Two modes, both on a temp copy so FL's file lock never touches the user's
file:

- ``song`` (default): FL's File > Export > Wave file... in Song mode, driven
  through System Events (``AutodriveExportUi``). The F11.0.1 spike found
  that FL's command-line render always renders the current *pattern*, and
  no flag or saved-project state switches it to the song.
- ``pattern``: FL's command-line render (``-R -Ewav``). Headless-ish and
  fine for one-pattern renders such as kit-sound checks.

If FL is already running we refuse with ``FL_BUSY`` (song mode always;
pattern mode unless ``RENDER_WHILE_RUNNING_SAFE``); either way we never
kill an FL we didn't start.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import soundfile as sf

from .fl_app import FL_APP_ENV, resolve_fl_app
from .logging_setup import get_logger

__all__ = [
    "FL_APP_ENV",
    "RenderCache",
    "RenderError",
    "RenderResult",
    "render_to_wav",
    "resolve_fl_app",
]

CACHE_DIR_ENV = "FLSTUDIO_MCP_RENDER_CACHE"
DEFAULT_CACHE_DIR = Path.home() / "Library" / "Caches" / "flstudio-mcp" / "renders"
DEFAULT_CACHE_CAP_BYTES = 2 * 1024**3
DEFAULT_TIMEOUT_S = 180.0
STABLE_S = 2.0
POLL_S = 0.25
RENDER_STEM = "render"
FL_PROCESS = "OsxFL"

# Set by the F11.0.1 spike: True only if a CLI render alongside an open FL
# session was observed to leave that session untouched.
RENDER_WHILE_RUNNING_SAFE = False

_LOG = get_logger("render")

RenderErrorCode = Literal["FL_APP_NOT_FOUND", "FL_BUSY", "RENDER_TIMEOUT", "RENDER_FAILED"]
Outcome = Literal["exited", "stable", "exported", "cached"]
RenderMode = Literal["song", "pattern"]


class RenderError(Exception):
    """Render failure with a machine-readable code (mapped onto ErrorCode)."""

    def __init__(self, code: RenderErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RenderResult:
    wav_path: Path
    duration_s: float
    file_size: int
    render_walltime_s: float
    cached: bool
    outcome: Outcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "wav_path": str(self.wav_path),
            "duration_s": round(self.duration_s, 3),
            "file_size": self.file_size,
            "render_walltime_s": round(self.render_walltime_s, 3),
            "cached": self.cached,
            "outcome": self.outcome,
        }


class Launched(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...


Launcher = Callable[[list[str]], Launched]


def _default_launcher(argv: list[str]) -> Launched:
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _default_is_fl_running() -> bool:
    """pgrep first: if the AppleScript process list fails we must still see FL."""
    if _fl_process_alive():
        return True
    from .installer.verify import detect_fl_processes

    return bool(detect_fl_processes())


def _fl_process_alive() -> bool:
    return subprocess.run(["pgrep", "-x", FL_PROCESS], capture_output=True).returncode == 0


def _default_kill_fl() -> None:
    """Stop the FL we launched and wait until it's gone (a half-dead FL breaks the next launch)."""
    subprocess.run(["pkill", "-x", FL_PROCESS], check=False, capture_output=True)
    deadline = time.monotonic() + 10.0
    while _fl_process_alive() and time.monotonic() < deadline:
        time.sleep(POLL_S)
    if _fl_process_alive():
        subprocess.run(["pkill", "-9", "-x", FL_PROCESS], check=False, capture_output=True)
        time.sleep(1.0)


def build_render_argv(fl_app: Path, flp: Path) -> list[str]:
    """FL CLI render invocation (flags confirmed by the F11.0.1 spike)."""
    return ["open", "-W", "-a", str(fl_app), "--args", "-R", "-Ewav", str(flp)]


class RenderCache:
    """WAVs keyed by sha256 of the FLP bytes, LRU-pruned under ``cap_bytes``."""

    def __init__(self, root: Path | None = None, cap_bytes: int = DEFAULT_CACHE_CAP_BYTES) -> None:
        env = os.environ.get(CACHE_DIR_ENV)
        self.root = root or (Path(env) if env else DEFAULT_CACHE_DIR)
        self.cap_bytes = cap_bytes

    @staticmethod
    def key_for(flp_bytes: bytes, mode: RenderMode = "song") -> str:
        return hashlib.sha256(flp_bytes + b"\0" + mode.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.wav"

    def get(self, key: str) -> Path | None:
        path = self._path(key)
        if path.is_file() and path.stat().st_size > 0:
            os.utime(path)  # LRU touch
            return path
        return None

    def put(self, key: str, src: Path) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        dst = self._path(key)
        shutil.move(str(src), dst)
        os.utime(dst)
        self.prune(keep=dst)
        return dst

    def prune(self, keep: Path | None = None) -> None:
        files = sorted(
            (p for p in self.root.glob("*.wav") if p.is_file()), key=lambda p: p.stat().st_mtime
        )
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= self.cap_bytes:
                break
            if keep is not None and path == keep:
                continue
            total -= path.stat().st_size
            path.unlink(missing_ok=True)


class ExportUi(Protocol):
    def render_song(self, flp: Path, *, timeout_s: float) -> None: ...


class AutodriveExportUi:
    """FL's File > Export > Wave file... in Song mode, via System Events (F11.0.1).

    Opens ``flp`` in a fresh FL, turns off Options > 'Typing keyboard to
    piano' (otherwise L plays a note), presses L (FL opens projects in
    Pattern mode), exports through the native Save panel (it defaults to
    ``<flp dir>/<flp stem>.wav``), starts the render with Return and waits
    for the WAV. The piano-typing preference is restored before returning.
    """

    MENU_PIANO = (
        'menu item "Typing keyboard to piano" of menu 1 of menu bar item "Options" of menu bar 1'
    )
    MENU_EXPORT_WAV = (
        'menu item "Wave file..." of menu 1 of menu item "Export" of menu 1 '
        'of menu bar item "File" of menu bar 1'
    )
    MENU_WELCOME = (
        'menu item "Welcome to FL Studio" of menu 1 of menu bar item "View" of menu bar 1'
    )
    MENU_CLOSE_PLUGINS = (
        'menu item "Close all plugin windows" of menu 1 of menu bar item "View" of menu bar 1'
    )
    SAVE_PANEL = 'splitter group 1 of window "Save"'

    def __init__(self, fl_app: Path, *, process: str = FL_PROCESS) -> None:
        self.fl_app = fl_app
        self.tell = f'tell application "System Events" to tell process "{process}"'

    def _osa(self, script: str) -> str:
        proc = subprocess.run(
            ["osascript", "-e", f"{self.tell} to {script}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return proc.stdout.strip()

    def _checked(self, menu_item: str) -> bool:
        mark = self._osa(f'get value of attribute "AXMenuItemMarkChar" of {menu_item}')
        return mark not in ("", "missing value")

    def _windows(self) -> str:
        return self._osa("get name of every window")

    def _wait(self, ready: Callable[[], bool], timeout_s: float, what: str) -> None:
        deadline = time.monotonic() + timeout_s
        while not ready():
            if time.monotonic() >= deadline:
                raise RenderError(
                    "RENDER_TIMEOUT",
                    f"timed out waiting for {what} (FL windows: {self._windows() or 'none'})",
                )
            time.sleep(POLL_S)

    def render_song(self, flp: Path, *, timeout_s: float) -> None:
        from re_harness import autodrive

        wav = flp.with_suffix(".wav")
        deadline = time.monotonic() + timeout_s
        subprocess.run(["open", "-a", str(self.fl_app), str(flp)], check=False, capture_output=True)
        # The project window is titled "<project> - FL Studio <N>"; the splash isn't.
        self._wait(lambda: " - FL Studio" in self._windows(), 90.0, "FL to open the project")
        time.sleep(5.0)  # let plugins finish loading
        autodrive.dismiss_modals()
        # Generator plugins may open their editor windows on load; they take
        # keyboard focus and would swallow the L keystroke.
        self._osa(f"click {self.MENU_CLOSE_PLUGINS}")
        time.sleep(0.5)
        # FL shows its welcome screen on every launch (a killed FL never saves
        # "don't show again"); it also steals focus.
        if self._checked(self.MENU_WELCOME):
            self._osa(f"click {self.MENU_WELCOME}")
            time.sleep(0.5)
        autodrive.activate_fl_studio()
        _LOG.info("render_song ready", extra={"windows": self._windows()})
        piano_on_bool = self._checked(self.MENU_PIANO)
        try:
            if piano_on_bool:
                self._osa(f"click {self.MENU_PIANO}")
                time.sleep(0.5)
            self._osa('keystroke "l"')  # Pattern -> Song mode
            time.sleep(1.0)
            _LOG.info("render_song export", extra={"piano_was_on": piano_on_bool})
            self._osa(f"click {self.MENU_EXPORT_WAV}")
            self._wait(lambda: "Save" in self._windows().split(", "), 20.0, "the export Save panel")
            self._osa(f'click (first button of {self.SAVE_PANEL} whose title is "Save")')
            self._wait(lambda: "Rendering to" in self._windows(), 20.0, "FL's render window")
            self._osa(
                'perform action "AXRaise" of (first window whose name starts with "Rendering to")'
            )
            time.sleep(0.3)
            self._osa("keystroke return")  # Start
            remaining = max(5.0, deadline - time.monotonic())
            self._wait(
                lambda: (
                    wav.is_file()
                    and wav.stat().st_size > 1000
                    and "Rendering" not in self._windows()
                ),
                remaining,
                "the render to finish",
            )
        finally:
            if piano_on_bool:
                self._osa(f"click {self.MENU_PIANO}")


def _wait_for_output(
    proc: Launched,
    wav: Path,
    *,
    timeout_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> Literal["exited", "stable", "timeout"]:
    """Done when FL exits, or when the WAV stops growing for STABLE_S."""
    deadline = clock() + timeout_s
    last_size = -1
    stable_since: float | None = None
    while True:
        if proc.poll() is not None:
            return "exited"
        size = wav.stat().st_size if wav.is_file() else -1
        now = clock()
        if size > 0 and size == last_size:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= STABLE_S:
                return "stable"
        else:
            stable_since = None
        last_size = size
        if now >= deadline:
            return "timeout"
        sleep(POLL_S)


def _result(path: Path, *, walltime: float, outcome: Outcome) -> RenderResult:
    info = sf.info(str(path))
    return RenderResult(
        wav_path=path,
        duration_s=float(info.duration),
        file_size=path.stat().st_size,
        render_walltime_s=walltime,
        cached=outcome == "cached",
        outcome=outcome,
    )


def render_to_wav(
    flp_path: Path,
    *,
    mode: RenderMode = "song",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    fl_app: Path | None = None,
    cache: RenderCache | None = None,
    force: bool = False,
    export_ui: ExportUi | None = None,
    launcher: Launcher | None = None,
    is_fl_running: Callable[[], bool] | None = None,
    kill_fl: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RenderResult:
    """Render ``flp_path`` to WAV (cached by content hash). Raises RenderError."""
    flp_path = Path(flp_path)
    data = flp_path.read_bytes()
    cache = cache or RenderCache()
    key = cache.key_for(data, mode)
    if not force:
        hit = cache.get(key)
        if hit is not None:
            return _result(hit, walltime=0.0, outcome="cached")

    app = resolve_fl_app(fl_app)
    if not app.exists():
        raise RenderError("FL_APP_NOT_FOUND", f"{app} not found")
    already_running = (is_fl_running or _default_is_fl_running)()
    if already_running and (mode == "song" or not RENDER_WHILE_RUNNING_SAFE):
        raise RenderError("FL_BUSY", "FL Studio is already running")
    owns_fl = not already_running

    if mode == "song":
        ui = export_ui or AutodriveExportUi(app)
        with tempfile.TemporaryDirectory(prefix="flstudio-mcp-render-") as tmp:
            tmp_flp = Path(tmp) / f"{RENDER_STEM}.flp"
            tmp_flp.write_bytes(data)
            wav = tmp_flp.with_suffix(".wav")
            _LOG.info("render start (song export)", extra={"flp": str(flp_path)})
            started = clock()
            try:
                ui.render_song(tmp_flp, timeout_s=timeout_s)
            finally:
                (kill_fl or _default_kill_fl)()  # owns_fl: FL_BUSY guarantees it's ours
            walltime = clock() - started
            if not wav.is_file() or wav.stat().st_size == 0:
                raise RenderError("RENDER_FAILED", f"FL's export didn't write {wav.name}")
            dst = cache.put(key, wav)
        _LOG.info("render done", extra={"wav": str(dst), "walltime_s": round(walltime, 1)})
        return _result(dst, walltime=walltime, outcome="exported")

    with tempfile.TemporaryDirectory(prefix="flstudio-mcp-render-") as tmp:
        tmp_flp = Path(tmp) / f"{RENDER_STEM}.flp"
        tmp_flp.write_bytes(data)
        wav = tmp_flp.with_suffix(".wav")
        argv = build_render_argv(app, tmp_flp)
        _LOG.info("render start", extra={"flp": str(flp_path), "argv": argv})
        started = clock()
        proc = (launcher or _default_launcher)(argv)
        outcome = _wait_for_output(proc, wav, timeout_s=timeout_s, clock=clock, sleep=sleep)
        walltime = clock() - started
        if outcome != "exited":
            if owns_fl:
                (kill_fl or _default_kill_fl)()
            proc.terminate()
        if outcome == "timeout":
            raise RenderError("RENDER_TIMEOUT", f"no finished WAV after {timeout_s:.0f}s")
        if not wav.is_file() or wav.stat().st_size == 0:
            raise RenderError("RENDER_FAILED", f"FL exited without writing {wav.name}")
        dst = cache.put(key, wav)
    _LOG.info("render done", extra={"wav": str(dst), "walltime_s": round(walltime, 1)})
    return _result(dst, walltime=walltime, outcome=outcome)
