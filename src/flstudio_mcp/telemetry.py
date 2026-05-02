"""Telemetry adapter — no-op default; backend deferred to v1.x.

The shape of ``record_event`` is fixed (decision #15, MCP-SPEC.md): every
tool invocation logs ``kind``, ``ok``, ``duration_ms`` plus arbitrary
metadata. Today the no-op backend just routes events to the package
logger so they show up in ``logs://``. When PostHog/Plausible/etc. is
wired in v1.x, swap ``_BACKEND``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .logging_setup import get_logger

EVENT_LOGGER_CHILD = "telemetry"
TELEMETRY_ENV_VAR = "FLSTUDIO_MCP_TELEMETRY"

EventBackend = Callable[[str, bool, float, dict[str, Any]], None]


def _logger_backend(kind: str, ok: bool, duration_ms: float, meta: dict[str, Any]) -> None:
    extra: dict[str, Any] = {"kind": kind, "ok": ok, "duration_ms": duration_ms}
    extra.update(meta)
    get_logger(EVENT_LOGGER_CHILD).info("event", extra=extra)


_BACKEND: EventBackend = _logger_backend


def telemetry_enabled() -> bool:
    """Opt-in via ``FLSTUDIO_MCP_TELEMETRY=1``."""
    return os.environ.get(TELEMETRY_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def record_event(kind: str, ok: bool, duration_ms: float, **meta: Any) -> None:
    """Record one tool-invocation event.

    The default backend writes to the package logger so events appear in
    ``logs://``. A real telemetry backend is wired only when
    ``telemetry_enabled()`` returns True.
    """
    _logger_backend(kind, ok, duration_ms, meta)
    if telemetry_enabled() and _BACKEND is not _logger_backend:
        _BACKEND(kind, ok, duration_ms, meta)


def set_backend(backend: EventBackend | None) -> None:
    """Swap the telemetry backend. Pass ``None`` to revert to the no-op."""
    global _BACKEND
    _BACKEND = backend if backend is not None else _logger_backend
