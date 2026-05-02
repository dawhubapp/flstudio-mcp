"""Tests for the ``logs://recent`` MCP resource."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import AnyUrl

from flstudio_mcp import logging_setup, server
from flstudio_mcp.resources import logs as logs_resource


@pytest.fixture
def configured_logs(tmp_path: Path) -> Path:
    return logging_setup.configure_logging(log_dir=tmp_path / "logs")


def _flush() -> None:
    for handler in logging.getLogger(logging_setup.LOGGER_NAME).handlers:
        handler.flush()


def test_render_logs_payload_returns_json_array(configured_logs: Path) -> None:
    log = logging_setup.get_logger("t")
    log.info("hello", extra={"kind": "describe", "ok": True, "duration_ms": 1.0})
    _flush()
    payload = logs_resource.render_logs_payload(50)
    decoded = json.loads(payload)
    assert isinstance(decoded, list)
    assert decoded[-1]["msg"] == "hello"
    assert decoded[-1]["kind"] == "describe"


def test_render_logs_payload_caps_at_max(configured_logs: Path) -> None:
    payload = logs_resource.render_logs_payload(10_000)
    assert isinstance(json.loads(payload), list)


def test_render_logs_payload_negative_returns_empty(configured_logs: Path) -> None:
    assert json.loads(logs_resource.render_logs_payload(-5)) == []


def test_resource_registered_on_built_server(configured_logs: Path) -> None:
    instance = server.build_server()
    assert any(
        getattr(r, "uri", None) == AnyUrl(logs_resource.RESOURCE_URI)
        for r in instance._resource_manager.list_resources()
    )


@pytest.mark.anyio
async def test_resource_read_returns_json(configured_logs: Path) -> None:
    log = logging_setup.get_logger()
    log.info("call", extra={"kind": "describe", "ok": True, "duration_ms": 5})
    _flush()

    instance = server.build_server()
    contents = await instance.read_resource(logs_resource.RESOURCE_URI)
    items = list(contents)
    assert items, "resource returned no contents"
    text = items[0].content
    decoded = json.loads(text)
    assert isinstance(decoded, list)
    assert decoded[-1]["msg"] == "call"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
