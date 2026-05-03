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
    env = offline_tool.execute("set_tempo", {"path": "/x.flp"}, runtime=rt)
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
