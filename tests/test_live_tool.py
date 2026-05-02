"""Tests for the ``live_execute`` tool."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from flstudio_mcp import logging_setup, server, state
from flstudio_mcp.tools import live as live_tool


@dataclass
class _FakeResult:
    status: str = "ok"
    detail: str = ""
    artifact_path: str | None = None
    id: str = "x"


@dataclass
class FakeRuntime:
    responses: dict[str, _FakeResult] = field(default_factory=dict)
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def send(self, kind: str, args: dict[str, Any] | None = None, **_kw) -> _FakeResult:
        self.sent.append((kind, args or {}))
        if kind in self.responses:
            return self.responses[kind]
        return _FakeResult()


@pytest.fixture(autouse=True)
def _logs(tmp_path: Path) -> Path:
    return logging_setup.configure_logging(log_dir=tmp_path / "logs")


def _describe_payload() -> str:
    return json.dumps(
        {
            "tempo": 145000.0,
            "project_title": "demo",
            "flp_path": "/tmp/demo.flp",
            "channel_count": 8,
            "pattern_count": 3,
            "insert_count": 105,
            "api_version": "FL Studio 25.2.4",
        }
    )


def _state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "DEFAULT_STATE_DIR", state_dir)
    return state_dir


def test_describe_returns_envelope_with_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime(responses={"describe": _FakeResult(detail=_describe_payload())})
    env = live_tool.execute("describe", None, runtime=rt)
    assert env["ok"] is True
    assert env["kind"] == "describe"
    assert env["result"]["tempo_bpm"] == 145.0
    assert env["result"]["flp_path"] == "/tmp/demo.flp"
    assert "raw" not in env["result"]
    assert env["duration_ms"] >= 0
    assert len(env["log_id"]) == 12


def test_get_tempo_parses_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime(responses={"get_tempo": _FakeResult(detail="128.5")})
    env = live_tool.execute("get_tempo", None, runtime=rt)
    assert env["ok"] is True
    assert env["result"] == {"tempo_bpm": 128.5}


def test_get_tempo_error_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime(responses={"get_tempo": _FakeResult(status="error", detail="boom")})
    env = live_tool.execute("get_tempo", None, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "RuntimeError"
    assert "get_tempo failed" in env["result"]["message"]


def test_get_tempo_non_numeric(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime(responses={"get_tempo": _FakeResult(detail="oops")})
    env = live_tool.execute("get_tempo", None, runtime=rt)
    assert env["ok"] is False
    assert "non-numeric" in env["result"]["message"]


def test_list_apis_enumerates_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime()
    env = live_tool.execute("list_apis", None, runtime=rt)
    assert env["ok"] is True
    assert env["result"]["tool"] == live_tool.TOOL_NAME
    assert set(env["result"]["kinds"]) == set(live_tool.SUPPORTED_KINDS)
    assert rt.sent == []  # purely local


def test_unsupported_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime()
    env = live_tool.execute("nope", None, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "UNSUPPORTED_KIND"
    assert "describe" in env["result"]["supported"]


def test_envelope_logged_to_logs_resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime()
    env = live_tool.execute("list_apis", None, runtime=rt)
    for h in logging.getLogger(logging_setup.LOGGER_NAME).handlers:
        h.flush()
    entries = logging_setup.read_recent_log_lines(50)
    log_id = env["log_id"]
    matching = [e for e in entries if e.get("log_id") == log_id]
    assert any(e["msg"] == "live_execute begin" for e in matching)
    assert any(e["msg"] == "live_execute ok" for e in matching)
    # telemetry event also lands
    assert any(e.get("kind") == "live_execute.list_apis" and e.get("ok") for e in entries)


def test_tool_registered_on_built_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime()
    instance = server.build_server(runtime_factory=lambda: rt)
    tool_names = [t.name for t in instance._tool_manager.list_tools()]
    assert live_tool.TOOL_NAME in tool_names


@pytest.mark.anyio
async def test_tool_callable_via_fastmcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_dir(tmp_path, monkeypatch)
    rt = FakeRuntime(responses={"get_tempo": _FakeResult(detail="120.0")})
    instance = server.build_server(runtime_factory=lambda: rt)
    result = await instance.call_tool(live_tool.TOOL_NAME, {"kind": "get_tempo"})
    # FastMCP returns a tuple (content, structured); structured is the dict.
    structured = result[1] if isinstance(result, tuple) else result
    assert structured["ok"] is True
    assert structured["result"] == {"tempo_bpm": 120.0}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
