"""Live runtime — thin wrapper over re_harness IPC.

Sends a ``Command`` to FL Studio's MIDI script and waits for the
matching ``Result``. Auto-archives the cmd/result pair after each call
so the inbox doesn't fill up.

re_harness is a regular installed dependency (the ``re-harness`` pip
package extracted from ``python/tools/re_harness/``). Import is lazy
inside the dataclass so unit tests can exercise the runtime with an
in-memory fake inbox without instantiating the real IPC.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from re_harness.ipc import Command, Result


DEFAULT_TIMEOUT_S = 10.0


class InboxLike(Protocol):
    def write_command(self, cmd: Command) -> Any: ...
    def wait_for_result(
        self, cmd_id: str, *, timeout: float = ..., poll_interval: float = ...
    ) -> Result: ...
    def archive(self, cmd_id: str) -> None: ...


def _import_ipc():
    """Import + return the re_harness.ipc module."""
    import re_harness.ipc as ipc

    return ipc


def make_command_id() -> str:
    """Generate a unique command id (ms timestamp + short uuid)."""
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


@dataclass
class LiveRuntime:
    """Send commands to FL Studio over the file-based IPC."""

    inbox: InboxLike
    timeout_s: float = DEFAULT_TIMEOUT_S
    archive_after: bool = True
    _command_cls: Any = field(default=None, repr=False)

    def _command(self, kind: str, args: dict[str, Any] | None) -> Command:
        if self._command_cls is None:
            self._command_cls = _import_ipc().Command
        return self._command_cls(id=make_command_id(), kind=kind, args=args or {})

    def send(
        self,
        kind: str,
        args: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> Result:
        """Send a command and block until the FL script answers."""
        cmd = self._command(kind, args)
        timeout = self.timeout_s if timeout_s is None else timeout_s
        self.inbox.write_command(cmd)
        try:
            result = self.inbox.wait_for_result(cmd.id, timeout=timeout)
        finally:
            if self.archive_after:
                with contextlib.suppress(Exception):
                    self.inbox.archive(cmd.id)
        return result

    def noop(self, *, timeout_s: float | None = None) -> Result:
        """Cheap handshake — verifies FL + MIDI script are responsive."""
        return self.send("noop", timeout_s=timeout_s)


def default_runtime(timeout_s: float = DEFAULT_TIMEOUT_S) -> LiveRuntime:
    """Build a runtime backed by re_harness.default_inbox()."""
    ipc = _import_ipc()
    return LiveRuntime(inbox=ipc.default_inbox(), timeout_s=timeout_s)
