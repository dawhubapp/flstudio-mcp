"""IPC protocol between the harness orchestrator and FL Studio's MIDI script
(SPEC 1.2.1).

The FL MIDI script API is event-driven (``OnIdle``, ``OnMidiMsg``) — it can't
hold an open TCP socket cleanly, and most users don't want a firewall prompt.
So we use **file-based IPC**:

* Orchestrator writes one ``cmd_<id>.json`` per command into the harness
  inbox directory.
* FL MIDI script, polling the inbox from ``OnIdle``, reads the oldest
  command, executes it via FL's scripting API, then writes a matching
  ``result_<id>.json`` into the outbox.
* Orchestrator waits for the matching result (with a timeout), reads it,
  moves both files into ``processed/`` for forensics.

This module owns the schemas + (de)serialization + directory layout.

Directory layout
----------------

::

    <runtime>/
      inbox/
        cmd_0001.json
        cmd_0002.json
      outbox/
        result_0001.json
      processed/
        cmd_0001.json
        result_0001.json

Keeping each command as its own file is what makes the system crash-safe:
if FL crashes mid-catalog, re-running the orchestrator picks up where it
left off by skipping commands whose matching results already exist.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

# --------------------------------------------------------------------------- #
# Default runtime location — must match the FL-side MIDI script's default.
# FL's sandboxed Python can't reliably write to fresh subdirs of ~/Documents/,
# so the runtime dir sits next to the installed MIDI script inside FL's own
# Hardware tree (FL already writes there for settings).
# --------------------------------------------------------------------------- #

DEFAULT_FL_HARDWARE_DIR = (
    Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Hardware"
)
DEFAULT_RUNTIME_ROOT = Path(
    os.environ.get(
        "FLPDIFF_HARNESS_INBOX",
        str(DEFAULT_FL_HARDWARE_DIR / "flpdiff-harness" / "runtime"),
    )
)

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

CommandKind = Literal[
    "set_tempo",  # change project tempo (bpm float)
    "get_tempo",  # read current tempo (bpm in detail)
    "save",  # save to currently-open path
    "noop",  # handshake
    "describe",  # read-only project introspection (JSON in detail)
    # ---- Planned but not yet implemented in the FL script ----
    "open",
    "set_time_signature",
    "set_channel_volume",
    "set_channel_pan",
    "set_channel_name",
    "set_insert_volume",
    "set_insert_name",
    "add_pattern_note",
]

ResultStatus = Literal["ok", "error", "unsupported"]


@dataclass(frozen=True, slots=True)
class Command:
    """One command sent to the FL MIDI script.

    Attributes
    ----------
    id
        Monotonic, zero-padded integer string. Orchestrator-assigned.
    kind
        Which op to run.
    args
        Op-specific arguments (plain JSON types only).
    """

    id: str
    kind: CommandKind
    args: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"id": self.id, "kind": self.kind, "args": self.args},
            indent=2,
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class Result:
    """Response from the FL MIDI script."""

    id: str
    status: ResultStatus
    detail: str = ""
    artifact_path: str | None = None  # for save_as: path to the written FLP

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class IPCError(Exception):
    pass


class IPCValidationError(IPCError):
    pass


_COMMAND_KINDS = set(CommandKind.__args__)  # type: ignore[attr-defined]
_RESULT_STATUSES = set(ResultStatus.__args__)  # type: ignore[attr-defined]


def parse_command(text: str) -> Command:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IPCValidationError(f"command: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise IPCValidationError("command: root must be an object")
    for req in ("id", "kind"):
        if req not in raw:
            raise IPCValidationError(f"command: missing field {req!r}")
    if not isinstance(raw["id"], str) or not raw["id"]:
        raise IPCValidationError("command: id must be a non-empty string")
    if raw["kind"] not in _COMMAND_KINDS:
        raise IPCValidationError(
            f"command: unknown kind {raw['kind']!r}. Allowed: {sorted(_COMMAND_KINDS)}"
        )
    args = raw.get("args", {})
    if not isinstance(args, dict):
        raise IPCValidationError("command: args must be an object")
    return Command(id=raw["id"], kind=raw["kind"], args=args)


def parse_result(text: str) -> Result:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IPCValidationError(f"result: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise IPCValidationError("result: root must be an object")
    for req in ("id", "status"):
        if req not in raw:
            raise IPCValidationError(f"result: missing field {req!r}")
    if raw["status"] not in _RESULT_STATUSES:
        raise IPCValidationError(
            f"result: unknown status {raw['status']!r}. Allowed: {sorted(_RESULT_STATUSES)}"
        )
    return Result(
        id=raw["id"],
        status=raw["status"],
        detail=str(raw.get("detail", "")),
        artifact_path=raw.get("artifact_path"),
    )


# --------------------------------------------------------------------------- #
# Directory layout + round-trip helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Inbox:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    def ensure(self) -> None:
        for d in (self.inbox, self.outbox, self.processed):
            d.mkdir(parents=True, exist_ok=True)

    def command_path(self, cmd_id: str) -> Path:
        return self.inbox / f"cmd_{cmd_id}.json"

    def result_path(self, cmd_id: str) -> Path:
        return self.outbox / f"result_{cmd_id}.json"

    def write_command(self, cmd: Command) -> Path:
        self.ensure()
        path = self.command_path(cmd.id)
        # Atomic write: write to tmp + rename, so the poller never sees a
        # partially-written file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(cmd.to_json(), encoding="utf-8")
        tmp.replace(path)
        return path

    def read_result(self, cmd_id: str) -> Result | None:
        path = self.result_path(cmd_id)
        if not path.exists():
            return None
        return parse_result(path.read_text(encoding="utf-8"))

    def archive(self, cmd_id: str) -> None:
        self.ensure()
        for src in (self.command_path(cmd_id), self.result_path(cmd_id)):
            if src.exists():
                src.replace(self.processed / src.name)

    def pending_commands(self) -> list[Path]:
        """Commands not yet answered — for crash-recovery planning."""
        if not self.inbox.exists():
            return []
        out: list[Path] = []
        for p in sorted(self.inbox.glob("cmd_*.json")):
            cmd_id = p.stem[len("cmd_"):]
            if not self.result_path(cmd_id).exists():
                out.append(p)
        return out

    def wait_for_result(
        self,
        cmd_id: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Result:
        """Block until a matching result appears or the timeout elapses.

        Tolerates transient read-tears: because FL's sandbox denies
        ``os.replace``, the FL-side script writes result files in place
        rather than atomically via rename. A poll that lands mid-write
        sees truncated JSON — we treat that as "not ready yet" and try
        again, rather than propagating the validation error.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = self.read_result(cmd_id)
            except IPCValidationError:
                result = None  # torn read; retry
            if result is not None:
                return result
            time.sleep(poll_interval)
        raise TimeoutError(
            f"no result for command {cmd_id!r} after {timeout:.1f}s "
            f"(is the FL MIDI script running?)"
        )


def default_inbox() -> Inbox:
    """Return an :class:`Inbox` rooted at the default runtime location.

    Matches what the FL-side MIDI script polls by default, so a caller
    that just wants "talk to the running FL harness" can do
    ``default_inbox()`` without knowing the path.
    """
    return Inbox(root=DEFAULT_RUNTIME_ROOT)


__all__ = [
    "DEFAULT_RUNTIME_ROOT",
    "Command",
    "CommandKind",
    "IPCError",
    "IPCValidationError",
    "Inbox",
    "Result",
    "ResultStatus",
    "default_inbox",
    "parse_command",
    "parse_result",
]
