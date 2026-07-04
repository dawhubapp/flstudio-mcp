"""Tests for offline_execute tool dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from flstudio_mcp import logging_setup
from flstudio_mcp.runtime.offline import (
    BridgeResponse,
    NodeNotFoundError,
    OfflineRuntimeError,
)
from flstudio_mcp.tools import offline as offline_tool


@dataclass
class FakeOfflineRuntime:
    response: BridgeResponse | None = None
    raise_runtime: bool = False
    raise_node_missing: bool = False
    last_call: tuple[str, dict[str, Any]] | None = None

    def call(self, kind: str, args: dict[str, Any] | None = None) -> BridgeResponse:
        self.last_call = (kind, args or {})
        if self.raise_node_missing:
            raise NodeNotFoundError("no bridge")
        if self.raise_runtime:
            raise OfflineRuntimeError("subprocess died")
        if self.response is None:
            return BridgeResponse(ok=True, kind=kind, result={"echo": kind})
        return self.response


@pytest.fixture(autouse=True)
def _logs(tmp_path):
    return logging_setup.configure_logging(log_dir=tmp_path / "logs")


def test_list_apis_doesnt_spawn_runtime() -> None:
    rt = FakeOfflineRuntime()
    env = offline_tool.execute("list_apis", None, runtime=rt)
    assert env["ok"] is True
    assert "describe" in env["result"]["kinds"]
    assert rt.last_call is None  # never invoked the bridge


def test_describe_round_trip() -> None:
    rt = FakeOfflineRuntime(
        response=BridgeResponse(ok=True, kind="describe", result={"_type": "FLPProject"})
    )
    env = offline_tool.execute("describe", {"path": "/tmp/x.flp"}, runtime=rt)
    assert env["ok"] is True
    assert env["result"] == {"_type": "FLPProject"}
    assert rt.last_call == ("describe", {"path": "/tmp/x.flp"})


def test_path_required_for_non_local_kinds() -> None:
    rt = FakeOfflineRuntime()
    env = offline_tool.execute("get_tempo", {}, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "INVALID_ARGS"
    assert "path is required" in env["result"]["message"]
    assert rt.last_call is None


def test_unsupported_kind() -> None:
    rt = FakeOfflineRuntime()
    env = offline_tool.execute("delete_universe", {"path": "/x.flp"}, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "UNSUPPORTED_KIND"
    assert "describe" in env["result"]["extra"]["supported"]
    assert rt.last_call is None


def test_node_not_found_returns_with_install_hint() -> None:
    rt = FakeOfflineRuntime(raise_node_missing=True)
    env = offline_tool.execute("describe", {"path": "/x.flp"}, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "UNKNOWN"
    assert "no bridge" in env["result"]["message"]
    assert "FLSTUDIO_MCP_BRIDGE_CMD" in env["result"]["hint"]


def test_runtime_error_propagates_as_envelope() -> None:
    rt = FakeOfflineRuntime(raise_runtime=True)
    env = offline_tool.execute("describe", {"path": "/x.flp"}, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "UNKNOWN"
    assert "subprocess died" in env["result"]["message"]


def test_bridge_returned_error_is_mapped() -> None:
    rt = FakeOfflineRuntime(
        response=BridgeResponse(
            ok=False,
            kind="describe",
            error="FILE_NOT_FOUND",
            message="flp file not found: /no.flp",
        )
    )
    env = offline_tool.execute("describe", {"path": "/no.flp"}, runtime=rt)
    assert env["ok"] is False
    # FILE_NOT_FOUND from bridge → INVALID_ARGS for the LLM
    assert env["result"]["error"] == "INVALID_ARGS"
    assert env["result"]["extra"]["bridge_error"] == "FILE_NOT_FOUND"


def test_bridge_returned_unsupported_propagates() -> None:
    rt = FakeOfflineRuntime(
        response=BridgeResponse(
            ok=False,
            kind="set_tempo",
            error="UNSUPPORTED_KIND",
            message="write kinds gated on serializer",
        )
    )
    env = offline_tool.execute("describe", {"path": "/x.flp"}, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "UNSUPPORTED_KIND"


@pytest.mark.parametrize(
    "kind",
    ["get_tempo", "list_channels", "list_mixer", "list_patterns", "list_plugins"],
)
def test_all_read_kinds_path_through(kind: str) -> None:
    rt = FakeOfflineRuntime(response=BridgeResponse(ok=True, kind=kind, result=[]))
    env = offline_tool.execute(kind, {"path": "/x.flp"}, runtime=rt)
    assert env["ok"] is True
    assert env["kind"] == kind
    assert rt.last_call == (kind, {"path": "/x.flp"})


# --------------------------------------------------------------------------- #
# Phase 3.2 write-kind auto-snapshot
# --------------------------------------------------------------------------- #


def test_write_kind_takes_snapshot_before_bridge_call(tmp_path) -> None:
    from flstudio_mcp import snapshots

    flp = tmp_path / "demo.flp"
    flp.write_bytes(b"FLhd\x00\x06")
    store = snapshots.SnapshotStore(root=tmp_path / "snaps", file_open_check=False)
    offline_tool.set_snapshot_store(store)
    try:
        rt = FakeOfflineRuntime(
            response=BridgeResponse(
                ok=True,
                kind="set_tempo",
                result={"path": str(flp), "bytes_written": 6},
            )
        )
        env = offline_tool.execute("set_tempo", {"path": str(flp), "bpm": 145}, runtime=rt)
    finally:
        offline_tool.set_snapshot_store(None)

    assert env["ok"] is True
    # snapshot_id added on top of bridge result
    assert env["result"]["snapshot_id"].startswith("demo/")
    listed = store.list_snapshots(project_slug="demo")
    assert len(listed) == 1
    assert listed[0].kind == "set_tempo"


def test_write_kind_refuses_when_fl_holds_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    flp = tmp_path / "demo.flp"
    flp.write_bytes(b"FLhd")
    monkeypatch.setattr(offline_tool, "_file_open_in_fl", lambda _p: True)

    rt = FakeOfflineRuntime()
    env = offline_tool.execute("set_tempo", {"path": str(flp), "bpm": 145}, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "FL_DIALOG_BLOCKING"
    assert rt.last_call is None  # bridge never called


def test_write_kind_skips_fl_check_when_file_missing(tmp_path) -> None:
    """Bridge will return its own FILE_NOT_FOUND; we shouldn't lsof a missing file."""
    flp = tmp_path / "no_such.flp"
    rt = FakeOfflineRuntime(
        response=BridgeResponse(
            ok=False,
            kind="set_tempo",
            error="FILE_NOT_FOUND",
            message="not there",
        )
    )
    offline_tool.set_snapshot_store(
        __import__("flstudio_mcp.snapshots", fromlist=["SnapshotStore"]).SnapshotStore(
            root=tmp_path / "snaps", file_open_check=False
        )
    )
    try:
        env = offline_tool.execute("set_tempo", {"path": str(flp), "bpm": 145}, runtime=rt)
    finally:
        offline_tool.set_snapshot_store(None)
    # Bridge handled the missing-file case; we mapped FILE_NOT_FOUND → INVALID_ARGS
    assert env["ok"] is False
    assert env["result"]["error"] == "SNAPSHOT_FAILED"  # snapshot of missing file fails first


def test_set_pattern_name_write_kind_round_trip(tmp_path) -> None:
    from flstudio_mcp import snapshots

    flp = tmp_path / "demo.flp"
    flp.write_bytes(b"FLhd\x00\x06")
    store = snapshots.SnapshotStore(root=tmp_path / "snaps", file_open_check=False)
    offline_tool.set_snapshot_store(store)
    try:
        rt = FakeOfflineRuntime(
            response=BridgeResponse(
                ok=True,
                kind="set_pattern_name",
                result={"path": str(flp), "bytes_written": 6},
            )
        )
        env = offline_tool.execute(
            "set_pattern_name",
            {"path": str(flp), "iid": 1, "name": "Verse-1"},
            runtime=rt,
        )
    finally:
        offline_tool.set_snapshot_store(None)
    assert env["ok"] is True
    assert env["result"]["snapshot_id"].startswith("demo/")
    assert rt.last_call == (
        "set_pattern_name",
        {"path": str(flp), "iid": 1, "name": "Verse-1"},
    )


@pytest.mark.anyio
async def test_bridge_free_kinds_work_with_no_bridge_available() -> None:
    """Regression: the registered tool must not resolve a runtime for
    BRIDGE_FREE_KINDS.

    ``execute()`` never touches ``runtime`` for these kinds (see
    ``test_list_apis_doesnt_spawn_runtime`` above), but the dispatcher
    wired up by ``register()`` used to call ``runtime_factory()``
    unconditionally *before* dispatch — so on a host with no Node/bun on
    PATH (a from-scratch CI runner, unlike a dev box with the sibling
    flpdiff/ workspace checked out), even ``list_apis`` failed with
    NodeNotFoundError instead of answering directly.
    """
    from mcp.server.fastmcp import FastMCP

    def _factory() -> Any:
        raise NodeNotFoundError("no bridge on this host")

    server = FastMCP(name="test-offline")
    offline_tool.register(server, runtime_factory=_factory)

    result = await server.call_tool(offline_tool.TOOL_NAME, {"kind": "list_apis"})
    structured = result[1] if isinstance(result, tuple) else result
    assert structured["ok"] is True
    assert "list_apis" in structured["result"]["kinds"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
