"""``offline_execute`` MCP tool — read .flp files without FL running.

Phase 3.1 surface: read-only kinds delegated to the canonical TS
parser via the flpdiff bridge subprocess. Result envelopes mirror
``live_execute`` so LLM clients can treat the two namespaces
symmetrically.

Write kinds (set_tempo, set_channel_name, …) land in Phase 3.2 once
the flpdiff TS serializer (Phase 3.0.3) ships.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ..errors import ErrorCode, ToolError, hint_for
from ..logging_setup import get_logger
from ..runtime.offline import (
    BridgeResponse,
    NodeNotFoundError,
    OfflineRuntime,
    OfflineRuntimeError,
    default_runtime,
)
from ..telemetry import record_event

OfflineKind = Literal[
    "describe",
    "get_tempo",
    "list_channels",
    "list_mixer",
    "list_patterns",
    "list_plugins",
    "list_apis",
]
SUPPORTED_KINDS: tuple[str, ...] = (
    "describe",
    "get_tempo",
    "list_channels",
    "list_mixer",
    "list_patterns",
    "list_plugins",
    "list_apis",
)
TOOL_NAME = "offline_execute"

_LOG = get_logger("tools.offline")


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


def _do_list_apis() -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "kinds": list(SUPPORTED_KINDS),
        "note": (
            "v0.1 read-only surface. Mutation kinds gated on the "
            "flpdiff TS serializer (Phase 3.0.3 / 3.2)."
        ),
    }


def execute(
    kind: str,
    args: dict[str, Any] | None,
    *,
    runtime: OfflineRuntime,
) -> dict[str, Any]:
    """Run one ``offline_execute`` call. Returns the standard envelope."""
    args = args or {}
    log_id = _make_log_id()
    started = time.time()

    _LOG.info(
        "offline_execute begin",
        extra={"kind": kind, "log_id": log_id, "args_keys": sorted(args.keys())},
    )

    if kind not in SUPPORTED_KINDS:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(
                ErrorCode.UNSUPPORTED_KIND,
                f"unknown kind {kind!r}",
                hint=hint_for(ErrorCode.UNSUPPORTED_KIND),
                extra={"supported": list(SUPPORTED_KINDS)},
            ),
        )

    if kind == "list_apis":
        env = _envelope(
            ok=True, kind=kind, result=_do_list_apis(), started_at=started, log_id=log_id
        )
        record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
        _LOG.info("offline_execute ok", extra={"kind": kind, "log_id": log_id})
        return env

    path = args.get("path")
    if not isinstance(path, str) or not path:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(ErrorCode.INVALID_ARGS, "args.path is required (non-empty string)"),
        )

    try:
        response: BridgeResponse = runtime.call(kind, args)
    except NodeNotFoundError as exc:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(
                ErrorCode.UNKNOWN,
                str(exc),
                hint=(
                    "Set FLSTUDIO_MCP_BRIDGE_CMD to a working bridge invocation, "
                    "or install bun + the flpdiff sibling workspace, or "
                    "`npm i -g flpdiff` to put `flpdiff` on PATH."
                ),
            ),
        )
    except OfflineRuntimeError as exc:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(ErrorCode.UNKNOWN, str(exc)),
        )

    if not response.ok:
        # Map bridge-side error codes onto our taxonomy where useful.
        code = ErrorCode.UNKNOWN
        if response.error == "INVALID_ARGS":
            code = ErrorCode.INVALID_ARGS
        elif response.error == "UNSUPPORTED_KIND":
            code = ErrorCode.UNSUPPORTED_KIND
        elif response.error in {"FILE_NOT_FOUND", "READ_ERROR", "PARSE_ERROR"}:
            code = ErrorCode.INVALID_ARGS  # caller-fixable: wrong/corrupt path
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(
                code,
                response.message or response.error or "bridge error",
                extra={"bridge_error": response.error},
            ),
        )

    env = _envelope(ok=True, kind=kind, result=response.result, started_at=started, log_id=log_id)
    _LOG.info(
        "offline_execute ok",
        extra={"kind": kind, "log_id": log_id, "duration_ms": env["duration_ms"]},
    )
    record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
    return env


def _emit_error(
    *,
    kind: str,
    log_id: str,
    started: float,
    err: ToolError,
) -> dict[str, Any]:
    env = _envelope(ok=False, kind=kind, result=err.to_result(), started_at=started, log_id=log_id)
    _LOG.warning(
        "offline_execute %s",
        err.code.value,
        extra={
            "kind": kind,
            "log_id": log_id,
            "duration_ms": env["duration_ms"],
            "error_code": err.code.value,
        },
    )
    record_event(
        f"{TOOL_NAME}.{kind}",
        False,
        env["duration_ms"],
        log_id=log_id,
        error=err.code.value,
    )
    return env


def register(
    server: FastMCP,
    runtime_factory: Callable[[], OfflineRuntime] | None = None,
) -> None:
    """Register ``offline_execute`` on the server.

    ``runtime_factory`` is called per invocation; default uses
    ``offline.default_runtime()`` which discovers the bridge command
    each time (cheap — ~50 µs of path lookups).
    """
    factory = runtime_factory if runtime_factory is not None else default_runtime

    @server.tool(
        name=TOOL_NAME,
        description=(
            "Read a .flp file without FL Studio running. Delegates to the "
            "canonical TS parser via the flpdiff bridge subprocess.\n"
            "\n"
            "Supported kinds (v0.1, all read-only):\n"
            "  - describe(path): full FLP project as JSON.\n"
            "  - get_tempo(path): {tempo_bpm}.\n"
            "  - list_channels(path): channel summaries.\n"
            "  - list_mixer(path): mixer insert summaries.\n"
            "  - list_patterns(path): pattern summaries.\n"
            "  - list_plugins(path): flat list of plugins (channel + mixer scopes).\n"
            "  - list_apis: enumerate kinds.\n"
            "\n"
            "Mutation kinds land in Phase 3.2 once the flpdiff TS "
            "serializer (Phase 3.0.3) ships. Returns the same envelope "
            "shape as live_execute: {ok, kind, result, duration_ms, log_id}."
        ),
    )
    def offline_execute(kind: OfflineKind, args: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = factory()
        return execute(kind, args, runtime=runtime)
