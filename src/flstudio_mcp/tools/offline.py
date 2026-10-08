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
from pathlib import Path
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
from ..snapshots import (
    SnapshotError,
    SnapshotStore,
    _file_open_in_fl,
)
from ..snapshots import (
    default_store as default_snapshot_store,
)
from ..telemetry import record_event

OfflineKind = Literal[
    "describe",
    "get_tempo",
    "list_channels",
    "list_mixer",
    "list_patterns",
    "list_plugins",
    "list_arrangements",
    "list_tracks",
    "list_clips",
    "list_apis",
    # Epic 6 / F6.3 — smart discovery (token-saving fuzzy lookups)
    "find_channel_by_name",
    "find_insert_by_name",
    "find_pattern_by_name",
    "find_plugin_instances",
    # Epic 11 / F11.1.1 — Tier 0 symbolic beat checks (read-only)
    "check_beat",
    # Epic 11 / F11.2.7 — genre blueprint (no FLP path required)
    "get_blueprint",
    # Phase 3.2 write kinds
    "set_tempo",
    "set_pattern_name",
    "set_channel_name",
    "set_insert_name",
    "set_time_signature",
    # Phase 3.4.1 colors + routing
    "set_channel_color",
    "set_insert_color",
    "set_pattern_color",
    "set_channel_routing",
    # Phase 3.4.3 arrangement + track names + clone
    "set_arrangement_name",
    "set_track_name",
    "clone_pattern",
    # Phase 3.4.4a track color (byte-patch encoder)
    "set_track_color",
    # Phase 3.4.6 track grouping (parent/child collapsible tracks)
    "set_track_grouped",
    # Phase 3.4.4b playlist clip mutations
    "add_clip",
    "remove_clip",
    "move_clip",
    # Code-level Ableton-style reorganize (no LLM required)
    "reorganize_project",
    # Epic 5 / F2.1 — pattern notes (0xE0)
    "add_pattern_note",
    "set_pattern_notes",
    "remove_pattern_note",
    # Epic 5 / F2.2 — pattern controllers (0xDF)
    "add_pattern_controller",
    "set_pattern_controllers",
    "remove_pattern_controller",
    # Epic 5 / F2.3 — pattern + channel creation
    "create_pattern",
    "create_channel",
    # Epic 5 / F2.4 — native plugin params (Fruity Parametric EQ 2 prototype)
    "set_native_plugin_param",
    # Epic 6 / F6.1 — note transformations + pattern length writer
    "set_pattern_length",
    "transpose_pattern_notes",
    "quantize_pattern_notes",
    "humanize_velocities",
    "humanize_timings",
    "reverse_pattern_notes",
    "invert_pattern_notes",
    # Epic 6 / F6.2 — channel volume + pan
    "set_channel_volume",
    "set_channel_pan",
    "arrange_song",
    "instantiate_native_plugin",
    "set_channel_sample_path",
    # Epic 7 / F7.1 — factory sample browser (no FLP path required)
    "list_factory_samples",
    # Epic 9 / F9.4 — factory plugin-preset browser (no FLP path required)
    "list_factory_presets",
    # Epic 9 / F9.5 — load .fst plugin preset onto a target FLP
    "load_factory_preset",
]
SUPPORTED_KINDS: tuple[str, ...] = (
    "describe",
    "get_tempo",
    "list_channels",
    "list_mixer",
    "list_patterns",
    "list_plugins",
    "list_arrangements",
    "list_tracks",
    "list_clips",
    "list_apis",
    "find_channel_by_name",
    "find_insert_by_name",
    "find_pattern_by_name",
    "find_plugin_instances",
    "check_beat",
    "get_blueprint",
    "set_tempo",
    "set_pattern_name",
    "set_channel_name",
    "set_insert_name",
    "set_time_signature",
    "set_channel_color",
    "set_insert_color",
    "set_pattern_color",
    "set_channel_routing",
    "set_arrangement_name",
    "set_track_name",
    "clone_pattern",
    "set_track_color",
    "set_track_grouped",
    "add_clip",
    "remove_clip",
    "move_clip",
    "reorganize_project",
    "add_pattern_note",
    "set_pattern_notes",
    "remove_pattern_note",
    "add_pattern_controller",
    "set_pattern_controllers",
    "remove_pattern_controller",
    "create_pattern",
    "create_channel",
    "set_native_plugin_param",
    "set_pattern_length",
    "transpose_pattern_notes",
    "quantize_pattern_notes",
    "humanize_velocities",
    "humanize_timings",
    "reverse_pattern_notes",
    "invert_pattern_notes",
    "set_channel_volume",
    "set_channel_pan",
    "arrange_song",
    "instantiate_native_plugin",
    "set_channel_sample_path",
    "list_factory_samples",
    "list_factory_presets",
    "load_factory_preset",
)
WRITE_KINDS: frozenset[str] = frozenset(
    {
        "set_tempo",
        "set_pattern_name",
        "set_channel_name",
        "set_insert_name",
        "set_time_signature",
        "set_channel_color",
        "set_insert_color",
        "set_pattern_color",
        "set_channel_routing",
        "set_arrangement_name",
        "set_track_name",
        "clone_pattern",
        "set_track_color",
        "set_track_grouped",
        "add_clip",
        "remove_clip",
        "move_clip",
        "reorganize_project",
        "add_pattern_note",
        "set_pattern_notes",
        "remove_pattern_note",
        "add_pattern_controller",
        "set_pattern_controllers",
        "remove_pattern_controller",
        "create_pattern",
        "create_channel",
        "set_native_plugin_param",
        "set_pattern_length",
        "transpose_pattern_notes",
        "quantize_pattern_notes",
        "humanize_velocities",
        "humanize_timings",
        "reverse_pattern_notes",
        "invert_pattern_notes",
        "set_channel_volume",
        "set_channel_pan",
        "arrange_song",
        "instantiate_native_plugin",
        "set_channel_sample_path",
        "load_factory_preset",
    }
)
# Kinds ``execute()`` answers without ever touching ``runtime`` — pure
# Python (list_apis) or a bundled JSON manifest walk (factory browsers).
# The dispatcher must not resolve a bridge command for these so they
# keep working with no Node/bun on PATH (see register()).
BRIDGE_FREE_KINDS: frozenset[str] = frozenset(
    {"list_apis", "list_factory_samples", "list_factory_presets", "get_blueprint"}
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
    runtime: OfflineRuntime | None,
) -> dict[str, Any]:
    """Run one ``offline_execute`` call. Returns the standard envelope.

    ``runtime`` may be ``None`` only for kinds in ``BRIDGE_FREE_KINDS`` —
    they return before it's ever touched.
    """
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

    if kind == "list_factory_samples":
        # F7.1 — read-only browser, no FLP path required, no bridge call.
        from ..sample_browser import enumerate_factory_samples, to_dict

        category = args.get("category")
        query = args.get("query")
        limit_raw = args.get("limit", 100)
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 100
        force_walk = bool(args.get("force_walk", False))
        try:
            entries = enumerate_factory_samples(
                category=category if isinstance(category, str) else None,
                query=query if isinstance(query, str) else None,
                limit=limit,
                force_walk=force_walk,
            )
        except Exception as exc:
            return _emit_error(
                kind=kind,
                log_id=log_id,
                started=started,
                err=ToolError(
                    ErrorCode.UNKNOWN,
                    f"sample browser failed: {exc!r}",
                ),
            )
        env = _envelope(
            ok=True,
            kind=kind,
            result={"items": [to_dict(e) for e in entries], "count": len(entries)},
            started_at=started,
            log_id=log_id,
        )
        record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
        _LOG.info("offline_execute ok", extra={"kind": kind, "log_id": log_id, "n": len(entries)})
        return env

    if kind == "list_factory_presets":
        # F9.4 — read-only browser, no FLP path required, no bridge call.
        from ..preset_browser import enumerate_factory_presets, to_dict

        plugin = args.get("plugin")
        kind_filter = args.get("kind")
        query = args.get("query")
        limit_raw = args.get("limit", 100)
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 100
        force_walk = bool(args.get("force_walk", False))
        if kind_filter not in (None, "generator", "effect", "channel_state"):
            return _emit_error(
                kind=kind,
                log_id=log_id,
                started=started,
                err=ToolError(
                    ErrorCode.INVALID_ARGS,
                    "args.kind must be one of 'generator' / 'effect' / 'channel_state'",
                ),
            )
        try:
            entries = enumerate_factory_presets(
                plugin=plugin if isinstance(plugin, str) else None,
                kind=kind_filter,
                query=query if isinstance(query, str) else None,
                limit=limit,
                force_walk=force_walk,
            )
        except Exception as exc:
            return _emit_error(
                kind=kind,
                log_id=log_id,
                started=started,
                err=ToolError(
                    ErrorCode.UNKNOWN,
                    f"preset browser failed: {exc!r}",
                ),
            )
        env = _envelope(
            ok=True,
            kind=kind,
            result={"items": [to_dict(e) for e in entries], "count": len(entries)},
            started_at=started,
            log_id=log_id,
        )
        record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
        _LOG.info("offline_execute ok", extra={"kind": kind, "log_id": log_id, "n": len(entries)})
        return env

    if kind == "get_blueprint":
        return _do_get_blueprint(args, log_id=log_id, started=started)

    path = args.get("path")
    if not isinstance(path, str) or not path:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(ErrorCode.INVALID_ARGS, "args.path is required (non-empty string)"),
        )

    if kind == "check_beat":
        return _do_check_beat(args, runtime=runtime, log_id=log_id, started=started)

    snapshot_id: str | None = None
    if kind in WRITE_KINDS:
        # Refuse if FL holds the file open — its in-memory state would
        # silently overwrite our edit on the next save.
        target = Path(path)
        if target.exists() and _file_open_in_fl(target):
            return _emit_error(
                kind=kind,
                log_id=log_id,
                started=started,
                err=ToolError(
                    ErrorCode.FL_DIALOG_BLOCKING,
                    f"FL Studio has {target} open — close the project before writing",
                    hint="Close the project in FL (File → Close), then retry.",
                ),
            )

        # Auto-snapshot pre-write (Phase 3.2.2)
        try:
            store = _resolve_snapshot_store_for_offline()
            meta = store.snapshot(target, kind=kind)
            snapshot_id = meta.snapshot_id
        except SnapshotError as exc:
            return _emit_error(
                kind=kind,
                log_id=log_id,
                started=started,
                err=ToolError(ErrorCode.SNAPSHOT_FAILED, str(exc)),
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

    result_payload = response.result
    if snapshot_id is not None and isinstance(result_payload, dict):
        # Embed the snapshot_id so callers can roll back via
        # live_execute(restore_snapshot, ...).
        result_payload = {**result_payload, "snapshot_id": snapshot_id}

    env = _envelope(ok=True, kind=kind, result=result_payload, started_at=started, log_id=log_id)
    _LOG.info(
        "offline_execute ok",
        extra={"kind": kind, "log_id": log_id, "duration_ms": env["duration_ms"]},
    )
    record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
    return env


def _do_get_blueprint(args: dict[str, Any], *, log_id: str, started: float) -> dict[str, Any]:
    """F11.2.7 — the genre's blueprint as JSON (forms as [section, bars] lists)."""
    from ..music.blueprints import BlueprintError, available_genres, load_blueprint

    kind = "get_blueprint"
    genre = args.get("genre")
    try:
        if not isinstance(genre, str):
            raise BlueprintError(
                f"args.genre is required; available: {', '.join(available_genres())}"
            )
        blueprint = load_blueprint(genre)
    except BlueprintError as exc:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(ErrorCode.INVALID_ARGS, str(exc)),
        )
    blueprint["forms"] = {
        name: [[section, bars] for section, bars in form]
        for name, form in blueprint["forms"].items()
    }
    env = _envelope(ok=True, kind=kind, result=blueprint, started_at=started, log_id=log_id)
    record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
    _LOG.info("offline_execute ok", extra={"kind": kind, "log_id": log_id})
    return env


def _do_check_beat(
    args: dict[str, Any],
    *,
    runtime: OfflineRuntime | None,
    log_id: str,
    started: float,
) -> dict[str, Any]:
    """F11.1.1 — describe via the bridge, then run the Tier 0 symbolic checks."""
    from ..beat_check import GENRES, ROLES, check_beat

    kind = "check_beat"

    def invalid(message: str) -> dict[str, Any]:
        return _emit_error(
            kind=kind,
            log_id=log_id,
            started=started,
            err=ToolError(ErrorCode.INVALID_ARGS, message),
        )

    genre = args.get("genre")
    if genre not in GENRES:
        return invalid(f"args.genre must be one of {list(GENRES)}")
    key = args.get("key")
    if key is not None and not isinstance(key, str):
        return invalid("args.key must be a string like 'F#m' or 'D dorian'")
    roles_raw = args.get("roles") or {}
    if not isinstance(roles_raw, dict):
        return invalid("args.roles must be an object {channel_iid: role}")
    roles: dict[int, str] = {}
    for raw_iid, role in roles_raw.items():
        try:
            iid = int(raw_iid)
        except (TypeError, ValueError):
            return invalid(f"args.roles keys must be channel iids, got {raw_iid!r}")
        if role not in ROLES:
            return invalid(f"unknown role {role!r}; roles: {list(ROLES)}")
        roles[iid] = role

    described = execute("describe", {"path": args["path"]}, runtime=runtime)
    if not described["ok"]:
        return {**described, "kind": kind}
    try:
        result = check_beat(described["result"], genre, key=key, roles=roles or None)
    except ValueError as exc:
        return invalid(str(exc))
    env = _envelope(ok=True, kind=kind, result=result.to_dict(), started_at=started, log_id=log_id)
    record_event(f"{TOOL_NAME}.{kind}", True, env["duration_ms"], log_id=log_id)
    return env


_SNAPSHOT_STORE_OVERRIDE: SnapshotStore | None = None


def set_snapshot_store(store: SnapshotStore | None) -> None:
    """Override the snapshot store used for write-kind auto-snapshots."""
    global _SNAPSHOT_STORE_OVERRIDE
    _SNAPSHOT_STORE_OVERRIDE = store


def _resolve_snapshot_store_for_offline() -> SnapshotStore:
    if _SNAPSHOT_STORE_OVERRIDE is not None:
        return _SNAPSHOT_STORE_OVERRIDE
    return default_snapshot_store()


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
            "  - find_channel_by_name(path, query, fuzzy?): substring search by channel name (default fuzzy=true). Returns [{iid, name, kind}].\n"
            "  - find_insert_by_name(path, query, fuzzy?): substring search by mixer insert name. Returns [{index, name}].\n"
            "  - find_pattern_by_name(path, query, fuzzy?): substring search by pattern name. Returns [{id, name, notes}].\n"
            "  - find_plugin_instances(path, plugin_name): locate every instance of a plugin (channel + mixer scopes). Returns [{scope, channel_index|insert_index+slot_index, name}].\n"
            "  - check_beat(path, genre, key?, roles?): Tier 0 musicality checks on the arranged beat — register per role (plugin channels), velocity spread, chord voicings, genre groove (house four-on-the-floor drop, trap half-time backbeat), section contrast. genre='house'|'trap'; key like 'F#m' or 'D dorian'; roles={channel_iid: role} overrides name-based inference. Returns {ok, issues: [{severity, message, hint, role?, section?}], metrics}.\n"
            "  - get_blueprint(genre): arrangement rules for a genre (no path needed). Returns {phrase_bars, bpm, layers: {role: [layer]}, forms: {beat_32|beat_64|song: [[section, bars], ...]}, sections: {name: {bars: [allowed], energy, parts: {'role.layer': share of the section's bars it plays}}}, transitions: {phrase_change, phrase_end_any, phrase_end: {device: rate}, last_bar_rest, pre_drop_silent}}. genre='house'.\n"
            "\n"
            "Mutation kinds (auto-snapshot before write; result includes snapshot_id):\n"
            "  - set_tempo(path, bpm): replace the modern 0x9C tempo event.\n"
            "  - set_pattern_name(path, iid, name): rename pattern at 1-based iid.\n"
            "  - set_channel_name(path, iid, name): rename channel by 0-based iid.\n"
            "  - set_insert_name(path, index, name): rename mixer insert (0=master).\n"
            "  - set_time_signature(path, numerator, denominator): set project time sig.\n"
            "  - add_pattern_note(path, pattern_id, position, channel_iid, length, key, ...): append a note to a pattern. position+length in PPQ ticks; key 0..131 (60=C5).\n"
            "  - set_pattern_notes(path, pattern_id, notes): replace every note on a pattern with the given list. Empty list clears all notes.\n"
            "  - remove_pattern_note(path, pattern_id, index): drop the note at the given 0-based index in stream order.\n"
            "  - add_pattern_controller(path, pattern_id, position, channel, value, flags?): add a keyframe-automation controller (0xDF) — position in PPQ ticks, value as float32.\n"
            "  - set_pattern_controllers(path, pattern_id, controllers): replace all controller events on the pattern.\n"
            "  - remove_pattern_controller(path, pattern_id, index): drop the controller at the given 0-based index.\n"
            "  - create_pattern(path, name?): create a new empty pattern; returns {pattern_id} = max(existing) + 1.\n"
            "  - create_channel(path, name?, kind?): create a new empty channel; kind defaults to 'sampler' (also: instrument, automation, layer); returns {channel_iid} = max(existing) + 1.\n"
            "  - set_native_plugin_param(path, scope, param, value, ...): patch one parameter of a native FL plugin's 0xD5 state blob. Supports Fruity Parametric EQ 2 (param='main_level' | 'band' band=1..7 field='level'|'freq'|'width'), Fruity Reeverb 2 + Fruity Limiter (param='param' param_index=0..N). value is normalized 0..1. scope='channel' or 'mixer_slot'.\n"
            "  - set_pattern_length(path, pattern_id, ticks): write/update the pattern-length scalar (0xA4) in PPQ ticks.\n"
            "  - transpose_pattern_notes(path, pattern_id, semitones, channel_iid?): shift every note's key by N semitones; optional channel filter; clamps to [0, 131].\n"
            "  - quantize_pattern_notes(path, pattern_id, grid_ticks, strength?): snap note positions to nearest grid multiple. strength 0..1 (default 1 = full snap). Auto-grows pattern length if any note now ends past it.\n"
            "  - humanize_velocities(path, pattern_id, range, seed?): add ±range jitter to velocities. Clamps to [1, 127]. Seed defaults to wall-clock time.\n"
            "  - humanize_timings(path, pattern_id, range_ticks, seed?): add ±range_ticks jitter to positions. Clamps to >=0. Auto-grows pattern length.\n"
            "  - reverse_pattern_notes(path, pattern_id): mirror notes in time about pattern length (or fallback notesEndTick when length=0).\n"
            "  - invert_pattern_notes(path, pattern_id, axis_key?): mirror keys about axis_key (default 60); clamps to [0, 131].\n"
            "  - set_channel_volume(path, iid, value): set a channel's volume slider. value normalized 0..1 (FL default 0.78 = 10000/12800).\n"
            "  - set_channel_pan(path, iid, value): set a channel's pan slider. value bipolar -1..+1 (-1 = full left, 0 = center, +1 = full right).\n"
            "  - arrange_song(path, arrangement, structure, track_index?, beats_per_bar?): lay out a sequence of pattern clips on one track. structure = [{pattern_id, bars, position_ticks?}]. Positions computed sequentially from bars * beats_per_bar * ppq unless overridden. Default track_index=0, beats_per_bar=4.\n"
            "  - instantiate_native_plugin(path, donor_path, plugin_name, insert_index, slot_marker): splice a plugin from `donor_path` (any FLP that already has the plugin baked) into `path` at master/insert_index slot_marker. Returns {fl_ipc_slot_index = slot_marker + 1}. Best-effort: FL UI recognition works; IPC binding may fail in some cases (R17). Use UNSUPPORTED_PLUGIN / PLUGIN_INSTANTIATE_FAILED error path to recover.\n"
            "  - set_channel_sample_path(path, iid, sample_path): set the sample loaded on a sampler channel. sample_path is FL-token form (e.g. '%FLStudioFactoryData%/Data/Patches/Packs/Drums/Kicks/909 Kick.wav'). Use list_factory_samples to discover token paths.\n"
            "  - list_factory_samples(category?, query?, limit?, force_walk?): list FL factory wavs from the bundled manifest (~3k samples). category = top-level pack folder (Drums/FLEX/Instruments/Loops/Risers/...); query = filename substring; limit defaults to 100, max 500. force_walk=true rebuilds from filesystem (slow, only if user has third-party packs). Returns {items: [{token, category, subcategory, filename, size_bytes}], count}.\n"
            "  - list_factory_presets(plugin?, kind?, query?, limit?, force_walk?): list FL native plugin presets (.fst) from the bundled manifest (~7k presets). plugin = case-insensitive plugin name substring (e.g. 'DX10', 'Reeverb'); kind = 'generator' (synth → channel splice), 'effect' (mixer-slot splice), or 'channel_state' (Channel presets dir); query = preset-name substring; limit defaults to 100, max 500. Returns {items: [{path, plugin, preset_name, category, kind, size_bytes}], count}. Path is FL-token form ready to pass to load_factory_preset.\n"
            "  - load_factory_preset(path, fst_path, kind, name?, insert_index?, slot_marker?): splice a .fst plugin preset into `path`. fst_path accepts FL token form (e.g. '%FLStudioFactoryData%/Data/Patches/Plugin presets/Generators/Fruity DX10/Steel Guitar.fst') or absolute filesystem path. kind='generator' splices as a new channel (returns {channel_iid}); kind='effect' splices into mixer slot at (insert_index, slot_marker) and returns {fl_ipc_slot_index = slot_marker + 1}. Optional `name` overrides the new channel's display name for generators.\n"
            "  - set_channel_color / set_insert_color / set_pattern_color / set_track_color: set color of a channel/insert/pattern/track. **All take args.color = {r, g, b, a?}** with **r/g/b as 0..255 INTEGERS** (NOT 0..1 floats). Optional alpha defaults to 0. Example: `set_channel_color(path, iid=0, color={r: 233, g: 75, b: 60})` for red. set_insert_color takes `index` (0=master). set_pattern_color takes `iid`. set_track_color takes `arrangement` + `track`.\n"
            "  - set_channel_routing(path, iid, target_insert): route a channel to a mixer insert. **NOTE: param is `target_insert` (NOT `insert_index`).**\n"
            "Plus: set_arrangement_name, set_track_name, set_track_grouped, clone_pattern, add_clip, remove_clip, move_clip, reorganize_project. See list_apis for the full set.\n"
            "Refuses with FL_DIALOG_BLOCKING when FL Studio currently has the "
            "file open (avoids in-memory state overwriting our edit).\n"
            "\n"
            "Returns the same envelope shape as live_execute: "
            "{ok, kind, result, duration_ms, log_id}."
        ),
    )
    def offline_execute(kind: OfflineKind, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind in BRIDGE_FREE_KINDS:
            # Don't resolve a bridge command for kinds that never use it —
            # otherwise a host with no Node/bun on PATH can't even answer
            # list_apis.
            return execute(kind, args, runtime=None)
        runtime = factory()
        return execute(kind, args, runtime=runtime)
