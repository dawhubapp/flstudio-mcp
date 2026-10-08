"""Render an FLP to WAV through FL Studio's command-line render (F7.2).

FL always renders a temp copy, so FL's file lock never touches the user's
file. If FL is already running we refuse with ``FL_BUSY`` unless the
F11.0.1 spike proved a parallel render leaves the open session alone
(``RENDER_WHILE_RUNNING_SAFE``); either way we never kill an FL we didn't
start. ``build_render_argv`` is the single place the CLI flags live.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import soundfile as sf

from .logging_setup import get_logger

APPLICATIONS = Path("/Applications")
FL_APP_ENV = "FLSTUDIO_MCP_FL_APP"
CACHE_DIR_ENV = "FLSTUDIO_MCP_RENDER_CACHE"
DEFAULT_CACHE_DIR = Path.home() / "Library" / "Caches" / "flstudio-mcp" / "renders"
DEFAULT_CACHE_CAP_BYTES = 2 * 1024**3
DEFAULT_TIMEOUT_S = 180.0
STABLE_S = 2.0
POLL_S = 0.25
RENDER_STEM = "render"
FL_PROCESS = "OsxFL"
_FL_APP_RE = re.compile(r"^FL Studio (\d+)\.app$")

# Set by the F11.0.1 spike: True only if a CLI render alongside an open FL
# session was observed to leave that session untouched.
RENDER_WHILE_RUNNING_SAFE = False

_LOG = get_logger("render")

RenderErrorCode = Literal["FL_APP_NOT_FOUND", "FL_BUSY", "RENDER_TIMEOUT", "RENDER_FAILED"]
Outcome = Literal["exited", "stable", "cached"]


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
    from .installer.verify import detect_fl_processes

    return bool(detect_fl_processes())


def _default_kill_fl() -> None:
    subprocess.run(["pkill", "-x", FL_PROCESS], check=False, capture_output=True)


def resolve_fl_app(fl_app: Path | None = None) -> Path:
    """Explicit path, else ``FLSTUDIO_MCP_FL_APP``, else the newest /Applications/FL Studio N.app."""
    if fl_app is not None:
        return fl_app
    env = os.environ.get(FL_APP_ENV)
    if env:
        return Path(env)
    installed = sorted(
        (
            (int(m.group(1)), path)
            for path in APPLICATIONS.glob("FL Studio *.app")
            if (m := _FL_APP_RE.match(path.name))
        ),
        reverse=True,
    )
    return installed[0][1] if installed else APPLICATIONS / "FL Studio.app"


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
    def key_for(flp_bytes: bytes) -> str:
        return hashlib.sha256(flp_bytes).hexdigest()

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
    timeout_s: float = DEFAULT_TIMEOUT_S,
    fl_app: Path | None = None,
    cache: RenderCache | None = None,
    force: bool = False,
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
    key = cache.key_for(data)
    if not force:
        hit = cache.get(key)
        if hit is not None:
            return _result(hit, walltime=0.0, outcome="cached")

    app = resolve_fl_app(fl_app)
    if not app.exists():
        raise RenderError("FL_APP_NOT_FOUND", f"{app} not found")
    already_running = (is_fl_running or _default_is_fl_running)()
    if already_running and not RENDER_WHILE_RUNNING_SAFE:
        raise RenderError("FL_BUSY", "FL Studio is already running")
    owns_fl = not already_running

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
