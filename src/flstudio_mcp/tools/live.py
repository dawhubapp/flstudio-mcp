"""``live_execute`` MCP tool — read-only v0.1 surface.

Phase 1.3 ships three kinds: ``describe``, ``get_tempo``, ``list_apis``.
Mutation kinds land in Epic 2.

Every call returns the standard envelope::

    {"ok": bool, "kind": str, "result": Any, "duration_ms": float, "log_id": str}

``log_id`` is a short opaque token correlating to the entry written to
``logs://recent`` for the call.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .. import state as state_mod
from ..installer import midi_script as midi_installer
from ..logging_setup import get_logger
from ..runtime.live import LiveRuntime
from ..telemetry import record_event

LiveKind = Literal["describe", "get_tempo", "list_apis", "install_script"]
SUPPORTED_KINDS: tuple[str, ...] = ("describe", "get_tempo", "list_apis", "install_script")
TOOL_NAME = "live_execute"

_LOG = get_logger("tools.live")


def _make_log_id() -> str:
    return uuid.uuid4().hex[:12]


def _envelope(
    *,
    ok: bool,
    kind: str,
    result: Any,
    started_at: float,
    log_id: str,
) -> dict[str, Any]:
    duration_ms = round((time.time() - started_at) * 1000.0, 3)
    return {
        "ok": ok,
        "kind": kind,
        "result": result,
        "duration_ms": duration_ms,
        "log_id": log_id,
    }


def _do_describe(runtime: LiveRuntime) -> dict[str, Any]:
    project = state_mod.describe_active_project(runtime)
    payload = project.to_dict()
    payload.pop("raw", None)
    return payload


def _do_get_tempo(runtime: LiveRuntime) -> dict[str, Any]:
    result = runtime.send("get_tempo")
    if getattr(result, "status", None) != "ok":
        raise RuntimeError(
            f"get_tempo failed: status={getattr(result, 'status', '?')!r} "
            f"detail={getattr(result, 'detail', '')[:200]!r}"
        )
    detail = getattr(result, "detail", "") or ""
    try:
        bpm = float(detail.strip())
    except ValueError as exc:
        raise RuntimeError(f"get_tempo returned non-numeric detail: {detail!r}") from exc
    return {"tempo_bpm": bpm}


def _do_list_apis() -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "kinds": list(SUPPORTED_KINDS),
        "note": (
            "v0.1 read-only surface. Mutation kinds (set_tempo, "
            "set_channel_name, save, …) land in Epic 2."
        ),
    }


def _do_install_script(args: dict[str, Any]) -> dict[str, Any]:
    """Re-run the MIDI script installer. Optional ``prefer_symlink`` arg."""
    prefer_symlink = bool(args.get("prefer_symlink", False))
    result = midi_installer.install_midi_script(prefer_symlink=prefer_symlink)
    return result.to_dict()


def execute(
    kind: str,
    args: dict[str, Any] | None,
    *,
    runtime: LiveRuntime,
) -> dict[str, Any]:
    """Run one ``live_execute`` call. Returns the standard envelope."""
    args = args or {}
    log_id = _make_log_id()
    started = time.time()

    _LOG.info(
        "live_execute begin",
        extra={"kind": kind, "log_id": log_id, "args_keys": sorted(args.keys())},
    )

    try:
        if kind == "describe":
            result = _do_describe(runtime)
        elif kind == "get_tempo":
            result = _do_get_tempo(runtime)
        elif kind == "list_apis":
            result = _do_list_apis()
        elif kind == "install_script":
            result = _do_install_script(args)
        else:
            envelope = _envelope(
                ok=False,
                kind=kind,
                result={
                    "error": "UNSUPPORTED_KIND",
                    "message": f"unknown kind {kind!r}",
                    "supported": list(SUPPORTED_KINDS),
                },
                started_at=started,
                log_id=log_id,
            )
            _LOG.warning(
                "live_execute unsupported_kind",
                extra={"kind": kind, "log_id": log_id, "duration_ms": envelope["duration_ms"]},
            )
            record_event(
                f"{TOOL_NAME}.{kind}",
                False,
                envelope["duration_ms"],
                log_id=log_id,
                error="UNSUPPORTED_KIND",
            )
            return envelope
    except Exception as exc:
        envelope = _envelope(
            ok=False,
            kind=kind,
            result={"error": type(exc).__name__, "message": str(exc)},
            started_at=started,
            log_id=log_id,
        )
        _LOG.exception(
            "live_execute error",
            extra={"kind": kind, "log_id": log_id, "duration_ms": envelope["duration_ms"]},
        )
        record_event(
            f"{TOOL_NAME}.{kind}",
            False,
            envelope["duration_ms"],
            log_id=log_id,
            error=type(exc).__name__,
        )
        return envelope

    envelope = _envelope(
        ok=True,
        kind=kind,
        result=result,
        started_at=started,
        log_id=log_id,
    )
    _LOG.info(
        "live_execute ok",
        extra={"kind": kind, "log_id": log_id, "duration_ms": envelope["duration_ms"]},
    )
    record_event(f"{TOOL_NAME}.{kind}", True, envelope["duration_ms"], log_id=log_id)
    return envelope


def register(server: FastMCP, runtime_factory: Callable[[], LiveRuntime]) -> None:
    """Register ``live_execute`` on the given server.

    ``runtime_factory`` is called per invocation so the runtime can be
    rebuilt cheaply if FL is restarted between calls.
    """

    @server.tool(
        name=TOOL_NAME,
        description=(
            "Execute a read-only operation against a running FL Studio instance.\n"
            "\n"
            "Supported kinds (v0.1):\n"
            "  - describe: report current project state (path, title, tempo, "
            "channel/pattern/insert counts, FL version).\n"
            "  - get_tempo: return current tempo in BPM.\n"
            "  - list_apis: enumerate every supported kind for this tool.\n"
            "  - install_script: re-run the bundled FL MIDI script installer "
            "(args: {prefer_symlink?: bool}).\n"
            "\n"
            "Returns: {ok, kind, result, duration_ms, log_id}. Use the "
            "logs://recent resource (filter by log_id) for full call detail."
        ),
    )
    def live_execute(kind: LiveKind, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return execute(kind, args, runtime=runtime_factory())
