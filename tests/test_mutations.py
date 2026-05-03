"""Tests for Phase 2.2 live mutation kinds."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from flstudio_mcp import snapshots, state
from flstudio_mcp.errors import ErrorCode, ToolError
from flstudio_mcp.tools import _mutations


@dataclass
class _FakeResult:
    status: str = "ok"
    detail: str = ""


_DEFAULT_DESCRIBE_PAYLOAD = json.dumps(
    {
        "tempo": 130000.0,
        "project_title": "demo",
        "flp_path": None,  # filled by fixture
        "channel_count": 8,
        "pattern_count": 1,
        "insert_count": 105,
        "api_version": "FL Studio 2025",
    }
)


@dataclass
class FakeRuntime:
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    next_status: str = "ok"
    next_detail: str = ""
    raise_timeout: bool = False
    describe_payload: str = _DEFAULT_DESCRIBE_PAYLOAD

    def send(self, kind: str, args: dict[str, Any] | None = None, **_kw) -> _FakeResult:
        self.sent.append((kind, args or {}))
        if self.raise_timeout:
            raise TimeoutError("noop deadline exceeded")
        if kind == "describe":
            return _FakeResult(status="ok", detail=self.describe_payload)
        return _FakeResult(status=self.next_status, detail=self.next_detail)

    def noop(self, *, timeout_s: float = 1.0) -> _FakeResult:
        return self.send("noop")


@pytest.fixture
def flp_file(tmp_path: Path) -> Path:
    f = tmp_path / "demo.flp"
    f.write_bytes(b"FLhd\x00\x06")
    return f


@pytest.fixture
def runtime(flp_file: Path) -> FakeRuntime:
    payload = json.loads(_DEFAULT_DESCRIBE_PAYLOAD)
    payload["flp_path"] = str(flp_file)
    return FakeRuntime(describe_payload=json.dumps(payload))


@pytest.fixture
def store(tmp_path: Path) -> snapshots.SnapshotStore:
    return snapshots.SnapshotStore(root=tmp_path / "snaps", file_open_check=False)


@pytest.fixture(autouse=True)
def _state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "DEFAULT_STATE_DIR", tmp_path / "state")


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #


def test_set_tempo_validates_bpm() -> None:
    table = _mutations._validation_dispatch_table()
    assert table["set_tempo"]({"bpm": 145.0}) == {"bpm": 145.0}


@pytest.mark.parametrize(
    "args, missing_field",
    [
        ({}, "bpm"),
        ({"bpm": -5}, "bpm"),
        ({"bpm": "fast"}, "bpm"),
    ],
)
def test_set_tempo_invalid(args: dict, missing_field: str) -> None:
    table = _mutations._validation_dispatch_table()
    with pytest.raises(ToolError) as exc:
        table["set_tempo"](args)
    assert exc.value.code == ErrorCode.INVALID_ARGS
    assert missing_field in exc.value.message


def test_set_channel_volume_clamped_range() -> None:
    table = _mutations._validation_dispatch_table()
    with pytest.raises(ToolError):
        table["set_channel_volume"]({"iid": 0, "value": 1.5})


def test_set_plugin_param_scope_literal() -> None:
    table = _mutations._validation_dispatch_table()
    with pytest.raises(ToolError):
        table["set_plugin_param"]({"index": 0, "param": 1, "value": 0.5, "scope": "bogus"})


def test_set_mixer_eq_optional_fields() -> None:
    table = _mutations._validation_dispatch_table()
    out = table["set_mixer_eq"]({"idx": 1, "band": 0, "gain": 0.0})
    assert out == {"idx": 1, "band": 0, "gain": 0.0}  # frequency/bandwidth omitted


def test_restore_snapshot_requires_id() -> None:
    table = _mutations._validation_dispatch_table()
    with pytest.raises(ToolError):
        table["restore_snapshot"]({})


# --------------------------------------------------------------------------- #
# Dispatcher: success path
# --------------------------------------------------------------------------- #


def test_set_tempo_snapshots_then_sends_ipc(runtime, store) -> None:
    result = _mutations.execute_mutation(
        "set_tempo", {"bpm": 145.0}, runtime=runtime, snapshot_store=store
    )
    assert "snapshot_id" in result
    assert result["snapshot_id"].startswith("demo/")
    # IPC sent the validated args
    assert ("set_tempo", {"bpm": 145.0}) in runtime.sent
    # Snapshot exists in store
    listed = store.list_snapshots(project_slug="demo")
    assert len(listed) == 1
    assert listed[0].snapshot_id == result["snapshot_id"]
    assert listed[0].kind == "set_tempo"


def test_set_channel_volume_round_trip(runtime, store) -> None:
    result = _mutations.execute_mutation(
        "set_channel_volume",
        {"iid": 3, "value": 0.5},
        runtime=runtime,
        snapshot_store=store,
    )
    assert "snapshot_id" in result
    assert ("set_channel_volume", {"iid": 3, "value": 0.5}) in runtime.sent


def test_save_skips_snapshot(runtime, store) -> None:
    """save IS the write — no point snapshotting before it."""
    result = _mutations.execute_mutation("save", {}, runtime=runtime, snapshot_store=store)
    assert "snapshot_id" not in result
    assert ("save", {}) in runtime.sent
    assert store.list_snapshots() == []


def test_get_plugin_info_skips_snapshot(runtime, store) -> None:
    """Read kind — no mutation, no snapshot."""
    result = _mutations.execute_mutation(
        "get_plugin_info",
        {"index": 0, "scope": "channel"},
        runtime=runtime,
        snapshot_store=store,
    )
    assert "snapshot_id" not in result
    assert store.list_snapshots() == []


# --------------------------------------------------------------------------- #
# Dispatcher: failure paths
# --------------------------------------------------------------------------- #


def test_unsupported_kind_raises(runtime, store) -> None:
    with pytest.raises(ToolError) as exc:
        _mutations.execute_mutation("nope", {}, runtime=runtime, snapshot_store=store)
    assert exc.value.code == ErrorCode.UNSUPPORTED_KIND


def test_invalid_args_short_circuits_before_snapshot(runtime, store) -> None:
    with pytest.raises(ToolError) as exc:
        _mutations.execute_mutation("set_tempo", {}, runtime=runtime, snapshot_store=store)
    assert exc.value.code == ErrorCode.INVALID_ARGS
    assert runtime.sent == []  # no IPC
    assert store.list_snapshots() == []  # no snapshot


def test_unsaved_project_returns_snapshot_failed(
    tmp_path: Path, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps(
        {
            "tempo": 130000.0,
            "project_title": None,
            "flp_path": None,
            "channel_count": 5,
        }
    )
    rt = FakeRuntime(describe_payload=payload)
    # Disable AppleScript fallback so test stays hermetic (otherwise it
    # finds the developer's real FL window on macOS)
    monkeypatch.setattr(state, "resolve_flp_path_from_fl_window", lambda: None)
    with pytest.raises(ToolError) as exc:
        _mutations.execute_mutation("set_tempo", {"bpm": 130}, runtime=rt, snapshot_store=store)
    assert exc.value.code == ErrorCode.SNAPSHOT_FAILED
    assert "save the project" in exc.value.message


def test_ipc_timeout_after_snapshot_includes_snapshot_id(runtime, store) -> None:
    """Snapshot survives even if FL never responds — caller can retry / restore."""
    # Pre-warm state cache so subsequent describe doesn't trigger raise_timeout
    state.describe_active_project(runtime)
    runtime.raise_timeout = True
    with pytest.raises(ToolError) as exc:
        _mutations.execute_mutation(
            "set_tempo", {"bpm": 145}, runtime=runtime, snapshot_store=store
        )
    assert exc.value.code == ErrorCode.IPC_TIMEOUT
    assert exc.value.extra is not None
    assert "snapshot_id" in exc.value.extra


def test_fl_returns_unsupported_status(runtime, store) -> None:
    runtime.next_status = "unsupported"
    runtime.next_detail = "not running inside FL Studio"
    with pytest.raises(ToolError) as exc:
        _mutations.execute_mutation(
            "set_tempo", {"bpm": 130}, runtime=runtime, snapshot_store=store
        )
    assert exc.value.code == ErrorCode.MIDI_SCRIPT_NOT_LOADED


def test_fl_returns_error_status_maps_to_unknown(runtime, store) -> None:
    runtime.next_status = "error"
    runtime.next_detail = "mixer.setCurrentTempo crashed"
    with pytest.raises(ToolError) as exc:
        _mutations.execute_mutation(
            "set_tempo", {"bpm": 130}, runtime=runtime, snapshot_store=store
        )
    assert exc.value.code == ErrorCode.UNKNOWN
    assert "crashed" in exc.value.message


# --------------------------------------------------------------------------- #
# restore_snapshot
# --------------------------------------------------------------------------- #


def test_restore_snapshot_writes_back(flp_file: Path, store) -> None:
    meta = store.snapshot(flp_file, kind="set_tempo")
    flp_file.write_bytes(b"mutated")

    result = _mutations.execute_restore_snapshot(
        {"snapshot_id": meta.snapshot_id}, snapshot_store=store
    )
    assert result["snapshot_id"] == meta.snapshot_id
    assert flp_file.read_bytes() == b"FLhd\x00\x06"


def test_restore_unknown_id(store) -> None:
    with pytest.raises(ToolError) as exc:
        _mutations.execute_restore_snapshot(
            {"snapshot_id": "ghost/20260101T000000-aaaaaa"}, snapshot_store=store
        )
    assert exc.value.code == ErrorCode.INVALID_ARGS
    assert exc.value.hint and "snapshots://recent" in exc.value.hint


def test_restore_refused_when_fl_holds_file(
    flp_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = snapshots.SnapshotStore(root=flp_file.parent / "snaps", file_open_check=True)
    meta = store.snapshot(flp_file, kind="set_tempo")
    monkeypatch.setattr(snapshots, "_file_open_in_fl", lambda _p: True)
    with pytest.raises(ToolError) as exc:
        _mutations.execute_restore_snapshot({"snapshot_id": meta.snapshot_id}, snapshot_store=store)
    assert exc.value.code == ErrorCode.FL_DIALOG_BLOCKING
    assert "Close the project" in (exc.value.hint or "")


# --------------------------------------------------------------------------- #
# Tool description
# --------------------------------------------------------------------------- #


def test_kinds_in_scope_includes_all_mutations() -> None:
    kinds = _mutations.kinds_in_scope()
    expected = {
        "set_tempo",
        "set_time_signature",
        "set_channel_volume",
        "set_channel_name",
        "set_insert_volume",
        "set_insert_name",
        "set_pattern_name",
        "set_plugin_param",
        "set_mixer_eq",
        "save",
        "get_plugin_info",
        "restore_snapshot",
    }
    assert expected <= set(kinds)


def test_tool_description_lists_every_kind() -> None:
    text = _mutations.tool_description_fragment()
    for k in _mutations.MUTATIONS:
        assert k in text
    for k in _mutations.READ_KINDS:
        assert k in text
    assert "restore_snapshot" in text
