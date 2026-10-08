"""``render_to_wav`` MCP tool (F7.2.3) — FL render + Tier 0 audio checks (F7.3.3).

A standalone tool, not an offline kind: rendering is a side effect, not a
file mutation, so there's no snapshot and no FL-open lock check (the
render works on a temp copy).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import render as render_mod
from ..audio_checks import SectionSpan
from ..audio_checks import analyze as analyze_audio
from ..beat_check import build_view
from ..errors import ErrorCode, ToolError
from ..logging_setup import get_logger
from ..runtime.offline import (
    NodeNotFoundError,
    OfflineRuntime,
    OfflineRuntimeError,
    default_runtime,
)
from ..telemetry import record_event

TOOL_NAME = "render_to_wav"
_LOG = get_logger("tools.render")
_CODES: dict[str, ErrorCode] = {
    "FL_APP_NOT_FOUND": ErrorCode.FL_APP_NOT_FOUND,
    "FL_BUSY": ErrorCode.FL_BUSY,
    "RENDER_TIMEOUT": ErrorCode.RENDER_TIMEOUT,
    "RENDER_FAILED": ErrorCode.RENDER_FAILED,
}
Renderer = Callable[..., render_mod.RenderResult]


def sections_for_audio(describe: dict[str, Any]) -> list[SectionSpan]:
    """Arrangement sections (from ``describe``) as time spans for audio checks."""
    view = build_view(describe)
    return [
        SectionSpan(
            name=s.name,
            start_s=view.seconds(s.start),
            end_s=view.seconds(s.start + s.length),
            energy=s.energy,
        )
        for s in view.sections
    ]


def _envelope(ok: bool, result: Any, started: float, log_id: str) -> dict[str, Any]:
    return {
        "ok": ok,
        "kind": TOOL_NAME,
        "result": result,
        "duration_ms": round((time.time() - started) * 1000.0, 3),
        "log_id": log_id,
    }


def _error(log_id: str, started: float, err: ToolError) -> dict[str, Any]:
    env = _envelope(False, err.to_result(), started, log_id)
    _LOG.warning("render_to_wav %s", err.code.value, extra={"log_id": log_id})
    record_event(TOOL_NAME, False, env["duration_ms"], log_id=log_id, error=err.code.value)
    return env


def execute(
    args: dict[str, Any],
    *,
    renderer: Renderer,
    runtime_factory: Callable[[], OfflineRuntime],
) -> dict[str, Any]:
    """Render ``args.path`` and (unless ``analyze`` is false) run audio checks."""
    log_id = uuid.uuid4().hex[:12]
    started = time.time()
    path = args.get("path")
    if not isinstance(path, str) or not path.lower().endswith(".flp") or not Path(path).is_file():
        return _error(
            log_id,
            started,
            ToolError(ErrorCode.INVALID_ARGS, "args.path must be an existing .flp file"),
        )
    _LOG.info("render_to_wav start (FL export, ~20-60 s)", extra={"log_id": log_id})
    try:
        result = renderer(Path(path), force=bool(args.get("force", False)))
    except render_mod.RenderError as exc:
        return _error(log_id, started, ToolError(_CODES[exc.code], exc.message))

    payload = result.to_dict()
    if args.get("analyze", True):
        sections: list[SectionSpan] = []
        try:
            response = runtime_factory().call("describe", {"path": path})
            if response.ok and isinstance(response.result, dict):
                sections = sections_for_audio(response.result)
            else:
                payload["analysis_note"] = (
                    f"describe failed ({response.error}); section checks skipped"
                )
        except (NodeNotFoundError, OfflineRuntimeError) as exc:
            payload["analysis_note"] = f"bridge unavailable ({exc}); section checks skipped"
        report = analyze_audio(result.wav_path, sections).to_dict()
        payload["issues"] = report.pop("issues")
        payload["metrics"] = report

    env = _envelope(True, payload, started, log_id)
    record_event(TOOL_NAME, True, env["duration_ms"], log_id=log_id)
    return env


def register(
    server: FastMCP,
    *,
    renderer: Renderer | None = None,
    runtime_factory: Callable[[], OfflineRuntime] | None = None,
) -> None:
    """Register ``render_to_wav`` on the server."""

    @server.tool(
        name=TOOL_NAME,
        description=(
            "Render a .flp's whole song to WAV. Opens a temp copy in FL Studio and "
            "drives File > Export (FL takes over the screen for ~20-60 s); results are "
            "cached by file hash, so re-rendering an unchanged file is instant. Refuses "
            "with FL_BUSY while FL Studio is open. With analyze=true (default) also returns audio metrics "
            "(peak, loudness, silent gaps, per-section level and sub/low/mid/high "
            "balance) and issues. Render once after composing, not after every edit. "
            "Returns {ok, kind, result: {wav_path, duration_s, file_size, "
            "render_walltime_s, cached, outcome, metrics?, issues?}, duration_ms, log_id}."
        ),
    )
    def render_to_wav(path: str, analyze: bool = True, force: bool = False) -> dict[str, Any]:
        return execute(
            {"path": path, "analyze": analyze, "force": force},
            renderer=renderer or render_mod.render_to_wav,
            runtime_factory=runtime_factory or default_runtime,
        )
