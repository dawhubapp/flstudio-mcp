"""Orchestrator for the FL Studio RE harness (SPEC 1.2.1).

The outer Python process. Responsible for:

1. Launching FL Studio with a base FLP.
2. Waiting for the MIDI script to come up (handshake via ``noop`` command).
3. Sending catalog commands through file-based IPC (:mod:`~.ipc`).
4. Capturing the saved, modified FLP.
5. Cleanly quitting or relaunching FL Studio between catalog entries.

FL Studio driver
----------------

The parts of this that actually *talk* to FL Studio are isolated behind
:class:`FLStudioDriver`. Real use instantiates :class:`MacOSFLStudioDriver`
which shells out to AppleScript + the FL binary. Tests instantiate
:class:`MockFLStudioDriver` which short-circuits to the IPC layer and lets
us exercise the whole orchestrator end-to-end without FL Studio.

Crash safety
------------

The harness is **resumable**. Each command lives as its own ``cmd_<id>.json``
file; a matching ``result_<id>.json`` means "already done". On restart the
orchestrator skips commands whose results already exist. Combined with the
per-modification base-file reset (each modification starts from the same
base FLP), that makes the whole discovery loop safe to Ctrl-C and re-run.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .ipc import Command, Inbox, Result

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Driver abstraction
# --------------------------------------------------------------------------- #


class FLStudioDriver(Protocol):
    """Everything the harness needs from FL Studio itself."""

    def launch(self, base_flp: Path) -> None: ...
    def quit(self) -> None: ...
    def is_running(self) -> bool: ...


# --------------------------------------------------------------------------- #
# macOS real driver
# --------------------------------------------------------------------------- #


DEFAULT_FL_APP = Path("/Applications/FL Studio.app")


@dataclass
class MacOSFLStudioDriver:
    """Launch / quit FL Studio on macOS via ``open`` + AppleScript.

    Not unit-tested directly (would require FL Studio). The orchestrator
    tests swap in :class:`MockFLStudioDriver`. This class is a thin shim
    so what's not tested is small + inspectable.
    """

    app_path: Path = DEFAULT_FL_APP
    launch_timeout: float = 60.0  # FL startup is slow on cold cache

    def launch(self, base_flp: Path) -> None:
        base_flp = base_flp.resolve()
        if not base_flp.exists():
            raise FileNotFoundError(f"base FLP missing: {base_flp}")
        subprocess.run(
            ["open", "-a", str(self.app_path), str(base_flp)],
            check=True,
        )
        # Wait until the FL process is visible. osascript is more reliable
        # than pgrep because FL reports a readable process name.
        deadline = time.monotonic() + self.launch_timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return
            time.sleep(0.5)
        raise TimeoutError(
            f"FL Studio did not start within {self.launch_timeout:.0f}s"
        )

    def quit(self) -> None:
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "FL Studio" to quit'],
                check=False,
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.warning("osascript quit timed out; falling back to killall")
            subprocess.run(["killall", "FL Studio"], check=False)

    def is_running(self) -> bool:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'application "FL Studio" is running',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "true"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


@dataclass
class Harness:
    """High-level orchestrator. Drives a sequence of commands against FL."""

    inbox: Inbox
    driver: FLStudioDriver
    # Per-command timeout when waiting for the FL script to answer.
    command_timeout: float = 60.0
    # Timeout on the initial handshake (noop round-trip).
    handshake_timeout: float = 120.0

    def __post_init__(self) -> None:
        self.inbox.ensure()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self, base_flp: Path) -> None:
        """Launch FL Studio on ``base_flp`` and wait for the MIDI script."""
        logger.info("launching FL Studio with %s", base_flp)
        self.driver.launch(base_flp)
        self._handshake()

    def stop(self) -> None:
        logger.info("quitting FL Studio")
        self.driver.quit()

    def _handshake(self) -> None:
        """Round-trip a ``noop`` to verify the MIDI script is alive."""
        cmd = Command(id="handshake", kind="noop")
        # Clear any stale result from prior runs.
        stale = self.inbox.result_path(cmd.id)
        if stale.exists():
            stale.unlink()
        self.inbox.write_command(cmd)
        try:
            self.inbox.wait_for_result(
                cmd.id,
                timeout=self.handshake_timeout,
                poll_interval=0.5,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                "FL MIDI script did not respond to noop handshake. "
                "Is the harness MIDI script installed and selected in FL Studio?"
            ) from exc
        finally:
            self.inbox.archive(cmd.id)

    # ------------------------------------------------------------------ #
    # Command execution
    # ------------------------------------------------------------------ #

    def run(self, cmd: Command, *, timeout: float | None = None) -> Result:
        """Send one command and return its result.

        Idempotent: if a result file for this ``cmd.id`` already exists
        (from a previous interrupted run), it is returned without
        re-sending the command. This is what makes the discovery loop
        resumable.
        """
        existing = self.inbox.read_result(cmd.id)
        if existing is not None:
            logger.info("reusing cached result for %s", cmd.id)
            self.inbox.archive(cmd.id)
            return existing

        self.inbox.write_command(cmd)
        try:
            result = self.inbox.wait_for_result(
                cmd.id,
                timeout=timeout or self.command_timeout,
            )
        finally:
            # Archive regardless of success so inbox never accumulates.
            self.inbox.archive(cmd.id)
        return result

    def run_sequence(self, commands: list[Command]) -> list[Result]:
        """Run each command in order, stopping on the first non-ok result.

        Returns all results collected up to and including the failure.
        """
        results: list[Result] = []
        for cmd in commands:
            result = self.run(cmd)
            results.append(result)
            if result.status != "ok":
                logger.warning(
                    "command %s returned %s: %s — aborting sequence",
                    cmd.id,
                    result.status,
                    result.detail,
                )
                break
        return results


# --------------------------------------------------------------------------- #
# Mock driver (tests + dry-run development)
# --------------------------------------------------------------------------- #


@dataclass
class MockFLStudioDriver:
    """Fake FL Studio for unit tests and local development.

    Instead of launching a real FL binary, this driver writes result files
    directly in response to inbox commands, simulating what the MIDI script
    would do. Behavior is parameterized:

    * ``ok_kinds`` — command kinds answered with status=ok.
    * ``unsupported_kinds`` — command kinds answered with status=unsupported.
    * ``save_writes_bytes`` — content written to the opened base file when
      a ``save`` command arrives. Models FL 25's reality: scripting save
      overwrites the currently-open FLP; the orchestrator reads from the
      path it opened.

    Runs a background thread polling the inbox — matching real FL's
    OnIdle-driven loop as closely as reasonable.
    """

    inbox: Inbox
    ok_kinds: frozenset[str] = frozenset()
    unsupported_kinds: frozenset[str] = frozenset()
    save_writes_bytes: bytes = b""
    poll_interval: float = 0.02

    _running: bool = False
    _thread: object = None  # threading.Thread | None, avoid import at class def
    _open_path: Path | None = None  # set by launch(); used by save handler

    def launch(self, base_flp: Path) -> None:
        from threading import Thread

        self.inbox.ensure()
        self._open_path = base_flp
        self._running = True
        self._thread = Thread(target=self._poll_loop, daemon=True)
        self._thread.start()  # type: ignore[union-attr]

    def quit(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)  # type: ignore[union-attr]

    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #

    def _poll_loop(self) -> None:
        while self._running:
            self._process_pending()
            time.sleep(self.poll_interval)

    def _process_pending(self) -> None:
        from .ipc import Result, parse_command

        for cmd_path in self.inbox.pending_commands():
            try:
                cmd = parse_command(cmd_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status: str = "ok"
            detail = ""
            artifact_path: str | None = None
            if cmd.kind in self.unsupported_kinds:
                status = "unsupported"
                detail = f"mock: {cmd.kind} flagged unsupported"
            elif self.ok_kinds and cmd.kind not in self.ok_kinds and cmd.kind != "noop":
                status = "error"
                detail = f"mock: {cmd.kind} not in ok_kinds"
            elif cmd.kind == "save":
                if self._open_path is None:
                    status = "error"
                    detail = "save: no project open"
                else:
                    self._open_path.parent.mkdir(parents=True, exist_ok=True)
                    self._open_path.write_bytes(self.save_writes_bytes)
                    artifact_path = str(self._open_path.resolve())
            # Write the result file atomically.
            out = Result(id=cmd.id, status=status, detail=detail, artifact_path=artifact_path)
            out_path = self.inbox.result_path(cmd.id)
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_text(out.to_json(), encoding="utf-8")
            tmp.replace(out_path)


# --------------------------------------------------------------------------- #
# Base-file reset helper
# --------------------------------------------------------------------------- #


def snapshot_base(base_flp: Path, into: Path) -> Path:
    """Copy the base FLP into a scratch location that the harness will
    modify. Returns the path of the copy.

    The convention across Phase 1.2 is: never open a base file directly.
    Always copy first so a stray save can't corrupt the committed base.
    """
    into.mkdir(parents=True, exist_ok=True)
    target = into / base_flp.name
    shutil.copy2(base_flp, target)
    return target


__all__ = [
    "DEFAULT_FL_APP",
    "FLStudioDriver",
    "Harness",
    "MacOSFLStudioDriver",
    "MockFLStudioDriver",
    "snapshot_base",
]
