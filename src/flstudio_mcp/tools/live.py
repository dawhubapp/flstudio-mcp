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
from ..errors import ErrorCode, ToolError, hint_for
from ..installer import iac as iac_installer
from ..installer import midi_script as midi_installer
from ..installer import verify as verify_installer
from ..installer import wire_fl as wire_fl_installer
from ..logging_setup import get_logger
from ..preflight import Preflight
from ..runtime.live import LiveRuntime
from ..telemetry import record_event

# Kinds that DON'T need FL running — they're local install / detection
# operations. Skip pre-flight for these so users can run them BEFORE
# FL exists or before the script is wired.
KINDS_SKIPPING_PREFLIGHT: frozenset[str] = frozenset(
    {
        "list_apis",
        "install_script",
        "check_iac",
        "enable_iac",
        "verify_setup",  # has its own internal chain
        "wire_input",
    }
)

LiveKind = Literal[
    "describe",
    "get_tempo",
    "list_apis",
    "install_script",
    "check_iac",
    "enable_iac",
    "verify_setup",
    "wire_input",
]
SUPPORTED_KINDS: tuple[str, ...] = (
    "describe",
    "get_tempo",
    "list_apis",
    "install_script",
    "check_iac",
    "enable_iac",
    "verify_setup",
    "wire_input",
)
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


def _emit_error(
    *,
    kind: str,
    log_id: str,
    started: float,
    err: ToolError,
    level: str = "error",
    log_traceback: bool = False,
) -> dict[str, Any]:
    """Build the ok=false envelope, log it, record telemetry."""
    envelope = _envelope(
        ok=False,
        kind=kind,
        result=err.to_result(),
        started_at=started,
        log_id=log_id,
    )
    log_extra = {
        "kind": kind,
        "log_id": log_id,
        "duration_ms": envelope["duration_ms"],
        "error_code": err.code.value,
    }
    if log_traceback:
        _LOG.exception("live_execute error", extra=log_extra)
    elif level == "warning":
        _LOG.warning("live_execute %s", err.code.value, extra=log_extra)
    else:
        _LOG.error("live_execute %s", err.code.value, extra=log_extra)
    record_event(
        f"{TOOL_NAME}.{kind}",
        False,
        envelope["duration_ms"],
        log_id=log_id,
        error=err.code.value,
    )
    return envelope


def _do_describe(runtime: LiveRuntime) -> dict[str, Any]:
    try:
        project = state_mod.describe_active_project(runtime)
    except TimeoutError as exc:
        raise ToolError(ErrorCode.IPC_TIMEOUT, str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(ErrorCode.MIDI_SCRIPT_NOT_LOADED, str(exc)) from exc
    payload = project.to_dict()
    payload.pop("raw", None)
    return payload


def _do_get_tempo(runtime: LiveRuntime) -> dict[str, Any]:
    try:
        result = runtime.send("get_tempo")
    except TimeoutError as exc:
        raise ToolError(ErrorCode.IPC_TIMEOUT, str(exc)) from exc
    if getattr(result, "status", None) != "ok":
        raise ToolError(
            ErrorCode.MIDI_SCRIPT_NOT_LOADED,
            f"get_tempo failed: status={getattr(result, 'status', '?')!r} "
            f"detail={getattr(result, 'detail', '')[:200]!r}",
        )
    detail = getattr(result, "detail", "") or ""
    try:
        bpm = float(detail.strip())
    except ValueError as exc:
        raise ToolError(
            ErrorCode.UNKNOWN,
            f"get_tempo returned non-numeric detail: {detail!r}",
        ) from exc
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
    """Re-run the MIDI script installer.

    By default installs into every detected ``FL Studio*/Settings/Hardware/``
    so multi-version installs all see the script. Args:

    * ``prefer_symlink: bool`` — symlink instead of copy.
    * ``hardware_dir: str`` — override target. When set, installs only
      into that single dir (no auto-discovery).
    """
    prefer_symlink = bool(args.get("prefer_symlink", False))
    override = args.get("hardware_dir")
    if override:
        from pathlib import Path

        result = midi_installer.install_midi_script(
            hardware_dir_path=Path(str(override)).expanduser(),
            prefer_symlink=prefer_symlink,
        )
        return {"installs": [result.to_dict()]}
    results = midi_installer.install_to_all_fl_versions(prefer_symlink=prefer_symlink)
    return {
        "installs": [r.to_dict() for r in results],
        "count": len(results),
    }


def _do_verify_setup(args: dict[str, Any], runtime: LiveRuntime) -> dict[str, Any]:
    skip_ui = bool(args.get("skip_ui", False))
    result = verify_installer.verify_setup(runtime, skip_ui=skip_ui)
    return result.to_dict()


def _do_wire_input(args: dict[str, Any]) -> dict[str, Any]:
    """Drive FL Settings via pyautogui to bind IAC input → flstudio-mcp script."""
    dry_run = bool(args.get("dry_run", False))
    coords_override = args.get("coords")
    coords = wire_fl_installer.WireCoords(**coords_override) if coords_override else None
    result = wire_fl_installer.wire_flstudio_mcp_input(coords=coords, dry_run=dry_run)
    return result.to_dict()


def _do_check_iac() -> dict[str, Any]:
    return iac_installer.check_iac_status().to_dict()


def _do_enable_iac() -> dict[str, Any]:
    status = iac_installer.enable_iac_via_ui_scripting()
    payload = status.to_dict()
    if not status.ok:
        payload["error"] = iac_installer.IAC_DRIVER_OFFLINE_ERROR
        payload["hint"] = (
            "Open Audio MIDI Setup (`open -a 'Audio MIDI Setup'`), "
            "double-click the IAC Driver row, and check 'Device is online'."
        )
    return payload


def execute(
    kind: str,
    args: dict[str, Any] | None,
    *,
    runtime: LiveRuntime,
    preflight: Preflight | None = None,
) -> dict[str, Any]:
    """Run one ``live_execute`` call. Returns the standard envelope.

    Pre-flight (Phase 2.4): for kinds that talk to FL, run a cached
    1-second noop ping before dispatching the real handler. Skips the
    check for purely-local kinds (``list_apis``, ``install_script``,
    ``check_iac``, ``enable_iac``, ``verify_setup``, ``wire_input``).
    """
    args = args or {}
    log_id = _make_log_id()
    started = time.time()

    _LOG.info(
        "live_execute begin",
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
            level="warning",
        )

    if preflight is not None and kind not in KINDS_SKIPPING_PREFLIGHT:
        pf = preflight.check()
        if not pf.ok:
            return _emit_error(
                kind=kind,
                log_id=log_id,
                started=started,
                err=ToolError(
                    pf.code or ErrorCode.PREFLIGHT_FAILED,
                    pf.detail,
                    extra={"cached": pf.cached},
                ),
                level="warning",
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
        elif kind == "check_iac":
            result = _do_check_iac()
        elif kind == "enable_iac":
            result = _do_enable_iac()
        elif kind == "verify_setup":
            result = _do_verify_setup(args, runtime)
        elif kind == "wire_input":
            result = _do_wire_input(args)
        else:  # unreachable — guarded above
            raise ToolError(ErrorCode.UNSUPPORTED_KIND, f"unknown kind {kind!r}")
    except ToolError as err:
        return _emit_error(kind=kind, log_id=log_id, started=started, err=err)
    except Exception as exc:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(ErrorCode.UNKNOWN, f"{type(exc).__name__}: {exc}"),
            log_traceback=True,
        )

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


def register(
    server: FastMCP,
    runtime_factory: Callable[[], LiveRuntime],
    *,
    preflight: Preflight | None = None,
) -> None:
    """Register ``live_execute`` on the given server.

    ``runtime_factory`` is called per invocation so the runtime can be
    rebuilt cheaply if FL is restarted between calls. ``preflight`` is
    a 5-second cached noop ping that runs before each FL-bound kind;
    pass ``None`` to disable (tests).
    """
    pf = preflight if preflight is not None else Preflight(runtime_factory=runtime_factory)

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
            "  - check_iac: report macOS IAC Driver state (online/offline/"
            "not_installed/unknown).\n"
            "  - enable_iac: best-effort UI-scripting attempt to flip IAC online.\n"
            "  - verify_setup: end-to-end check (IAC + script installed + FL "
            "running + Script output marker + IPC handshake). Args: "
            "{skip_ui?: bool} to skip the AppleScript-driven UI checks.\n"
            "  - wire_input: drive FL MIDI Settings via pyautogui to bind IAC "
            "Driver Bus 1 input to the flstudio-mcp script (one-time setup). "
            "Args: {dry_run?: bool, coords?: {update_scripts_btn:[x,y], "
            "iac_input_row:[x,y], enable_radio:[x,y], controller_dropdown:[x,y]}}.\n"
            "\n"
            "Returns: {ok, kind, result, duration_ms, log_id}. Use the "
            "logs://recent resource (filter by log_id) for full call detail."
        ),
    )
    def live_execute(kind: LiveKind, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return execute(kind, args, runtime=runtime_factory(), preflight=pf)
