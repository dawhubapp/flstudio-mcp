"""Pydantic arg schemas + dispatcher for mutating live_execute kinds.

Phase 2.2: every mutation goes through:
  1. pydantic validation → INVALID_ARGS on failure
  2. project state lookup (for snapshot path)
  3. auto-snapshot via SnapshotStore → SNAPSHOT_FAILED on failure
  4. IPC command to FL via LiveRuntime
  5. envelope includes snapshot_id

The ``save`` kind skips the auto-snapshot step (save IS the write).
``restore_snapshot`` is its own dispatcher path — no snapshot, just
read from the store and copy back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .. import state as state_mod
from ..errors import ErrorCode, ToolError
from ..logging_setup import get_logger
from ..runtime.live import LiveRuntime
from ..snapshots import (
    FileOpenInFLError,
    SnapshotError,
    SnapshotNotFoundError,
    SnapshotStore,
)

_LOG = get_logger("tools.mutations")


# --------------------------------------------------------------------------- #
# Per-kind argument schemas
# --------------------------------------------------------------------------- #


class SetTempoArgs(BaseModel):
    bpm: float = Field(..., gt=0, lt=10000, description="Tempo in BPM (0 < bpm < 10000).")


class SetTimeSignatureArgs(BaseModel):
    num: int = Field(..., gt=0, le=64, description="Numerator (beats per bar).")
    beat: int = Field(
        ..., gt=0, le=64, description="Denominator (note value: 4 = quarter, 8 = eighth, …)."
    )


class SetChannelVolumeArgs(BaseModel):
    iid: int = Field(..., ge=0, description="Channel-rack index, 0-based (0 = first channel).")
    value: float = Field(..., ge=0.0, le=1.0, description="Normalized volume (0.0-1.0).")


class SetChannelNameArgs(BaseModel):
    iid: int = Field(..., ge=0, description="Channel-rack index, 0-based.")
    name: str = Field(..., min_length=1, max_length=256)


class SetInsertVolumeArgs(BaseModel):
    idx: int = Field(
        ...,
        ge=0,
        description="Mixer insert index, 0-based (0 = Master, 1 = first user insert).",
    )
    value: float = Field(..., ge=0.0, le=1.0)


class SetInsertNameArgs(BaseModel):
    idx: int = Field(..., ge=0, description="Mixer insert index, 0-based (0 = Master).")
    name: str = Field(..., min_length=1, max_length=256)


class SetPatternNameArgs(BaseModel):
    iid: int = Field(
        ...,
        ge=1,
        description="Pattern index, 1-BASED (1 = first pattern). Verified against "
        "FL Studio 2025 — pattern numbering is 1-based in both UI and API; "
        "passing iid=0 silently no-ops.",
    )
    name: str = Field(..., min_length=1, max_length=256)


class SetPluginParamArgs(BaseModel):
    index: int = Field(..., ge=0, description="Channel-rack index OR mixer insert index.")
    param: int = Field(..., ge=0, description="Plugin parameter index (0-based).")
    value: float = Field(..., ge=0.0, le=1.0, description="Normalized value (0.0-1.0).")
    scope: Literal["channel", "mixer"] = "channel"
    slot: int = Field(
        default=-1, description="Mixer effect slot (0-9). Required when scope='mixer'."
    )


class GetPluginInfoArgs(BaseModel):
    index: int = Field(..., ge=0)
    scope: Literal["channel", "mixer"] = "channel"
    slot: int = Field(default=-1)


class SetMixerEqArgs(BaseModel):
    idx: int = Field(..., ge=0, description="Insert index.")
    band: int = Field(..., ge=0, le=2, description="EQ band: 0=low, 1=mid, 2=high.")
    frequency: float | None = Field(default=None, ge=0.0)
    gain: float | None = Field(default=None)
    bandwidth: float | None = Field(default=None, ge=0.0)


class SaveArgs(BaseModel):
    pass


class SetStepArgs(BaseModel):
    channel: int = Field(..., ge=0, description="Channel-rack index, 0-based.")
    step: int = Field(
        ..., ge=0, le=255, description="Step index inside the active pattern, 0-based."
    )
    on: bool = Field(..., description="True = lit step, False = empty.")


class GetPatternStepsArgs(BaseModel):
    channel: int = Field(..., ge=0, description="Channel-rack index, 0-based.")
    count: int = Field(default=16, ge=1, le=256, description="Steps to read (1..256).")


class ClearPatternStepsArgs(BaseModel):
    channel: int = Field(..., ge=0, description="Channel-rack index, 0-based.")
    count: int = Field(default=16, ge=1, le=256, description="Steps to clear (1..256).")


class RestoreSnapshotArgs(BaseModel):
    snapshot_id: str = Field(..., min_length=3)


# --------------------------------------------------------------------------- #
# Kind metadata
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MutationKind:
    name: str
    schema: type[BaseModel]
    snapshot: bool = True  # auto-snapshot before sending IPC
    forwards_args: bool = True  # pass validated args dict as IPC args


MUTATIONS: dict[str, MutationKind] = {
    "set_tempo": MutationKind("set_tempo", SetTempoArgs),
    "set_time_signature": MutationKind("set_time_signature", SetTimeSignatureArgs),
    "set_channel_volume": MutationKind("set_channel_volume", SetChannelVolumeArgs),
    "set_channel_name": MutationKind("set_channel_name", SetChannelNameArgs),
    "set_insert_volume": MutationKind("set_insert_volume", SetInsertVolumeArgs),
    "set_insert_name": MutationKind("set_insert_name", SetInsertNameArgs),
    "set_pattern_name": MutationKind("set_pattern_name", SetPatternNameArgs),
    "set_plugin_param": MutationKind("set_plugin_param", SetPluginParamArgs),
    "set_mixer_eq": MutationKind("set_mixer_eq", SetMixerEqArgs),
    "save": MutationKind("save", SaveArgs, snapshot=False),
    # Phase 2.3 step-sequencer ops
    "set_step": MutationKind("set_step", SetStepArgs),
    "clear_pattern_steps": MutationKind("clear_pattern_steps", ClearPatternStepsArgs),
}

READ_KINDS: dict[str, MutationKind] = {
    "get_plugin_info": MutationKind("get_plugin_info", GetPluginInfoArgs, snapshot=False),
    "get_pattern_steps": MutationKind("get_pattern_steps", GetPatternStepsArgs, snapshot=False),
}

ALL_KINDS: tuple[str, ...] = (*sorted(MUTATIONS), *sorted(READ_KINDS), "restore_snapshot")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate(schema: type[BaseModel], args: dict[str, Any]) -> dict[str, Any]:
    try:
        model = schema.model_validate(args)
    except ValidationError as exc:
        # Compact one-line summary per error.
        errors = [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()
        ]
        raise ToolError(
            ErrorCode.INVALID_ARGS,
            f"{schema.__name__} validation failed: {errors[0]['loc']} — {errors[0]['msg']}",
            extra={"errors": errors},
        ) from exc
    return model.model_dump(exclude_none=True)


# --------------------------------------------------------------------------- #
# Snapshot helpers
# --------------------------------------------------------------------------- #


def _resolve_flp_path(runtime: LiveRuntime) -> str:
    """Look up the currently-open FLP path; raise ToolError if unknown."""
    try:
        project = state_mod.resolve_active_project(runtime, use_cache=True)
    except TimeoutError as exc:
        raise ToolError(ErrorCode.IPC_TIMEOUT, str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(ErrorCode.MIDI_SCRIPT_NOT_LOADED, str(exc)) from exc
    if not project.flp_path:
        raise ToolError(
            ErrorCode.SNAPSHOT_FAILED,
            "FL Studio reports no open project file (project_title/flp_path empty); "
            "save the project to disk before mutating",
            hint=("In FL: File → Save (cmd-S) to a real path, then retry the mutation."),
        )
    return project.flp_path


def _snapshot_for(
    store: SnapshotStore, runtime: LiveRuntime, *, kind: str, command_id: str | None
) -> str:
    """Return snapshot_id; raise SNAPSHOT_FAILED ToolError on failure."""
    flp_path = _resolve_flp_path(runtime)
    try:
        meta = store.snapshot(
            flp_path,
            kind=kind,
            command_id=command_id,
        )
    except SnapshotError as exc:
        raise ToolError(ErrorCode.SNAPSHOT_FAILED, str(exc)) from exc
    return meta.snapshot_id


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def execute_mutation(
    kind: str,
    args: dict[str, Any] | None,
    *,
    runtime: LiveRuntime,
    snapshot_store: SnapshotStore,
) -> dict[str, Any]:
    """Run one mutation kind end-to-end. Returns the ``result`` dict.

    Raises :class:`ToolError` on any failure (caught by the live_execute
    boundary which converts to the ok=false envelope).
    """
    args = args or {}
    meta = MUTATIONS.get(kind) or READ_KINDS.get(kind)
    if meta is None:
        raise ToolError(
            ErrorCode.UNSUPPORTED_KIND,
            f"unknown mutation kind {kind!r}",
            extra={"supported": list(ALL_KINDS)},
        )

    validated = _validate(meta.schema, args)

    snapshot_id: str | None = None
    if meta.snapshot:
        snapshot_id = _snapshot_for(snapshot_store, runtime, kind=meta.name, command_id=None)

    try:
        result = runtime.send(meta.name, validated)
    except TimeoutError as exc:
        raise ToolError(
            ErrorCode.IPC_TIMEOUT,
            str(exc),
            extra={"snapshot_id": snapshot_id} if snapshot_id else None,
        ) from exc

    status = getattr(result, "status", None)
    detail = getattr(result, "detail", "") or ""
    if status != "ok":
        code = ErrorCode.MIDI_SCRIPT_NOT_LOADED if status == "unsupported" else ErrorCode.UNKNOWN
        raise ToolError(
            code,
            f"{meta.name} failed: status={status!r} detail={detail[:200]!r}",
            extra={"snapshot_id": snapshot_id} if snapshot_id else None,
        )

    payload: dict[str, Any] = {"detail": detail}
    if snapshot_id is not None:
        payload["snapshot_id"] = snapshot_id
    return payload


def execute_restore_snapshot(
    args: dict[str, Any] | None,
    *,
    snapshot_store: SnapshotStore,
) -> dict[str, Any]:
    """Restore a snapshot by id. Surface FILE_LOCKED / NOT_FOUND distinctly."""
    validated = _validate(RestoreSnapshotArgs, args or {})
    snapshot_id = validated["snapshot_id"]
    try:
        meta = snapshot_store.restore(snapshot_id)
    except SnapshotNotFoundError as exc:
        raise ToolError(
            ErrorCode.INVALID_ARGS,
            str(exc),
            hint="Call snapshots://recent to list available snapshot ids.",
        ) from exc
    except FileOpenInFLError as exc:
        raise ToolError(
            ErrorCode.FL_DIALOG_BLOCKING,
            str(exc),
            hint="Close the project in FL (File → Close), then retry restore_snapshot.",
        ) from exc
    except SnapshotError as exc:
        raise ToolError(ErrorCode.SNAPSHOT_FAILED, str(exc)) from exc
    return meta.to_dict()


def kinds_in_scope() -> tuple[str, ...]:
    """All Phase 2.2 kinds (mutations + reads + restore_snapshot)."""
    return ALL_KINDS


# --------------------------------------------------------------------------- #
# Tool description fragment (for inclusion in live_execute tool docstring)
# --------------------------------------------------------------------------- #


def tool_description_fragment() -> str:
    lines: list[str] = []
    lines.append("Live mutation kinds (auto-snapshot before sending; result includes snapshot_id):")
    for name in sorted(MUTATIONS):
        m = MUTATIONS[name]
        fields = ", ".join(_describe_fields(m.schema))
        snap = "" if m.snapshot else " (no snapshot — write itself)"
        lines.append(f"  - {name}({fields}){snap}")
    lines.append("Read kinds:")
    for name in sorted(READ_KINDS):
        m = READ_KINDS[name]
        fields = ", ".join(_describe_fields(m.schema))
        lines.append(f"  - {name}({fields})")
    lines.append(
        "Recovery:\n  - restore_snapshot(snapshot_id) — roll back to a "
        "previously-captured snapshot (see snapshots://recent)."
    )
    return "\n".join(lines)


def _describe_fields(schema: type[BaseModel]) -> list[str]:
    out: list[str] = []
    for name, field in schema.model_fields.items():
        anno = getattr(field.annotation, "__name__", str(field.annotation))
        if field.is_required():
            out.append(f"{name}: {anno}")
        else:
            out.append(f"{name}: {anno} = {field.default!r}")
    return out


# --------------------------------------------------------------------------- #
# Internal export for tests
# --------------------------------------------------------------------------- #


def _validation_dispatch_table() -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """For tests — direct access to per-kind validators."""
    table: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
    for k, m in {**MUTATIONS, **READ_KINDS}.items():
        table[k] = lambda args, _s=m.schema: _validate(_s, args)
    table["restore_snapshot"] = lambda args: _validate(RestoreSnapshotArgs, args)
    return table
