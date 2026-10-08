"""Tests for the get_blueprint offline kind (F11.2.7)."""

from __future__ import annotations

import json

import pytest

from flstudio_mcp import logging_setup
from flstudio_mcp.tools import offline as offline_tool


@pytest.fixture(autouse=True)
def _logs(tmp_path):
    return logging_setup.configure_logging(log_dir=tmp_path / "logs")


def test_returns_the_house_blueprint_without_a_bridge() -> None:
    env = offline_tool.execute("get_blueprint", {"genre": "house"}, runtime=None)
    assert env["ok"] is True and env["kind"] == "get_blueprint"
    bp = env["result"]
    assert bp["forms"]["beat_32"] == [["intro", 8], ["build", 8], ["drop", 16]]
    assert bp["sections"]["build"]["parts"]["snare.build"] == 1.0
    json.dumps(env, allow_nan=False)


@pytest.mark.parametrize("args", [{}, {"genre": "polka"}, {"genre": 3}])
def test_invalid_genre_lists_the_available_ones(args: dict) -> None:
    env = offline_tool.execute("get_blueprint", args, runtime=None)
    assert env["ok"] is False
    assert env["result"]["error"] == "INVALID_ARGS"
    assert "house" in env["result"]["message"]


def test_listed_bridge_free_and_read_only() -> None:
    assert "get_blueprint" in offline_tool.SUPPORTED_KINDS
    assert "get_blueprint" in offline_tool.BRIDGE_FREE_KINDS
    assert "get_blueprint" not in offline_tool.WRITE_KINDS
