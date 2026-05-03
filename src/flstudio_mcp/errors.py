"""Structured error codes for live_execute responses (Phase 2.4).

Every failure that the LLM might want to recover from gets a stable
machine-readable code + actionable hint. Codes are returned inside the
``result`` field of a `ok: false` envelope::

    {
      "ok": false,
      "kind": "set_tempo",
      "result": {
        "error": "FL_NOT_RUNNING",
        "message": "FL Studio process not found",
        "hint": "Open FL Studio, then retry."
      },
      "duration_ms": 12.3,
      "log_id": "..."
    }

Adding a new code is cheap; deleting one is a breaking change. Keep
this list small and load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    FL_NOT_RUNNING = "FL_NOT_RUNNING"
    MIDI_SCRIPT_NOT_LOADED = "MIDI_SCRIPT_NOT_LOADED"
    IAC_DRIVER_OFFLINE = "IAC_DRIVER_OFFLINE"
    IPC_TIMEOUT = "IPC_TIMEOUT"
    UNSUPPORTED_KIND = "UNSUPPORTED_KIND"
    INVALID_ARGS = "INVALID_ARGS"
    FL_DIALOG_BLOCKING = "FL_DIALOG_BLOCKING"
    SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    UNKNOWN = "UNKNOWN"


# Default actionable hints per code. Override per-call when a more
# specific message is available.
_DEFAULT_HINTS: dict[ErrorCode, str] = {
    ErrorCode.FL_NOT_RUNNING: ("Launch FL Studio (any 2024+ version), then retry."),
    ErrorCode.MIDI_SCRIPT_NOT_LOADED: (
        "FL is running but the flstudio-mcp script isn't responding. "
        "Open FL → Options → MIDI Settings → click 'Update MIDI scripts', "
        "then set IAC Driver Bus 1 input's Controller type to 'flstudio-mcp'."
    ),
    ErrorCode.IAC_DRIVER_OFFLINE: (
        "Open Audio MIDI Setup, double-click IAC Driver, tick 'Device is online'."
    ),
    ErrorCode.IPC_TIMEOUT: (
        "FL accepted the command but didn't respond in time. "
        "Try once more; if it persists, reload the MIDI script in FL."
    ),
    ErrorCode.UNSUPPORTED_KIND: ("Call live_execute(kind='list_apis') to see the supported kinds."),
    ErrorCode.INVALID_ARGS: "",
    ErrorCode.FL_DIALOG_BLOCKING: (
        "FL Studio has a modal dialog open (e.g. unsaved-changes prompt). "
        "Dismiss it manually, then retry."
    ),
    ErrorCode.SNAPSHOT_FAILED: (
        "Could not snapshot the .flp before mutating. Mutation aborted to "
        "avoid an unrecoverable state. Check disk space + permissions on "
        "~/Library/Application Support/flstudio-mcp/snapshots/."
    ),
    ErrorCode.PREFLIGHT_FAILED: (
        "Run live_execute(kind='verify_setup') for step-by-step diagnosis."
    ),
    ErrorCode.UNKNOWN: "",
}


def hint_for(code: ErrorCode) -> str:
    return _DEFAULT_HINTS.get(code, "")


@dataclass
class ToolError(Exception):
    """Raised inside tool handlers to short-circuit with a structured error.

    The ``execute`` boundary catches this and turns it into the
    `ok: false` envelope shape (no traceback leaks across MCP).
    """

    code: ErrorCode
    message: str
    hint: str | None = None
    extra: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        super().__init__(f"{self.code.value}: {self.message}")

    def to_result(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "error": self.code.value,
            "message": self.message,
            "hint": self.hint if self.hint is not None else hint_for(self.code),
        }
        if self.extra:
            out["extra"] = self.extra
        return out
