"""Pre-flight check for live_execute (Phase 2.4.2).

Cheap noop ping to FL with a 1-second timeout. Result cached for 5
seconds so back-to-back tool calls don't pay the IPC round-trip on
every invocation.

Disambiguates failure mode: if FL isn't running we return
``FL_NOT_RUNNING`` (no point pinging an absent script); if it's
running but noop times out we return ``MIDI_SCRIPT_NOT_LOADED`` so
the LLM can prompt the user to wire the Controller type.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .errors import ErrorCode
from .installer import verify as verify_installer
from .logging_setup import get_logger
from .runtime.live import LiveRuntime

DEFAULT_CACHE_TTL_S = 5.0
DEFAULT_NOOP_TIMEOUT_S = 1.0

_LOG = get_logger("preflight")


@dataclass
class PreflightResult:
    ok: bool
    code: ErrorCode | None
    detail: str
    cached: bool

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code.value if self.code else None,
            "detail": self.detail,
            "cached": self.cached,
        }


@dataclass
class Preflight:
    """5-second cached noop ping. Thread-unsafe (single-threaded MCP server)."""

    runtime_factory: Callable[[], LiveRuntime]
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S
    noop_timeout_s: float = DEFAULT_NOOP_TIMEOUT_S
    _last_run: float = 0.0
    _last_result: PreflightResult | None = None

    def check(self, *, force: bool = False) -> PreflightResult:
        now = time.time()
        if (
            not force
            and self._last_result is not None
            and (now - self._last_run) < self.cache_ttl_s
        ):
            return PreflightResult(
                ok=self._last_result.ok,
                code=self._last_result.code,
                detail=self._last_result.detail,
                cached=True,
            )

        result = self._run()
        self._last_run = now
        self._last_result = result
        return result

    def invalidate(self) -> None:
        """Force the next check() to re-probe (e.g. after a recovery action)."""
        self._last_result = None
        self._last_run = 0.0

    def _run(self) -> PreflightResult:
        # Fast path: is FL running at all? Cheap process enumeration via
        # System Events (~80 ms) — much faster than waiting for a noop
        # to time out.
        procs = verify_installer.detect_fl_processes()
        if not procs:
            return PreflightResult(
                ok=False,
                code=ErrorCode.FL_NOT_RUNNING,
                detail="no FL Studio process found",
                cached=False,
            )

        try:
            runtime = self.runtime_factory()
            result = runtime.noop(timeout_s=self.noop_timeout_s)
        except TimeoutError as exc:
            return PreflightResult(
                ok=False,
                code=ErrorCode.MIDI_SCRIPT_NOT_LOADED,
                detail=str(exc),
                cached=False,
            )
        except Exception as exc:
            _LOG.exception("preflight noop raised", extra={"error": type(exc).__name__})
            return PreflightResult(
                ok=False,
                code=ErrorCode.PREFLIGHT_FAILED,
                detail=f"{type(exc).__name__}: {exc}",
                cached=False,
            )

        if getattr(result, "status", None) != "ok":
            return PreflightResult(
                ok=False,
                code=ErrorCode.MIDI_SCRIPT_NOT_LOADED,
                detail=f"noop returned status={result.status!r}",
                cached=False,
            )

        return PreflightResult(ok=True, code=None, detail="noop ok", cached=False)
