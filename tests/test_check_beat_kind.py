"""Tests for the check_beat offline kind."""

from __future__ import annotations

import pytest

from flstudio_mcp import logging_setup
from flstudio_mcp.runtime.offline import BridgeResponse
from flstudio_mcp.tools import offline as offline_tool

from .beat_fixtures import good_house
from .test_offline_tool import FakeOfflineRuntime


@pytest.fixture(autouse=True)
def _logs(tmp_path):
    return logging_setup.configure_logging(log_dir=tmp_path / "logs")


def test_check_beat_runs_on_describe() -> None:
    rt = FakeOfflineRuntime(response=BridgeResponse(ok=True, kind="describe", result=good_house()))
    env = offline_tool.execute(
        "check_beat", {"path": "/x.flp", "genre": "house", "key": "Am"}, runtime=rt
    )
    assert env["ok"] is True and env["kind"] == "check_beat"
    assert env["result"]["ok"] is True
    assert env["result"]["metrics"]["total_bars"] == 32
    assert rt.last_call == ("describe", {"path": "/x.flp"})


def test_roles_override_via_string_keys() -> None:
    rt = FakeOfflineRuntime(response=BridgeResponse(ok=True, kind="describe", result=good_house()))
    env = offline_tool.execute(
        "check_beat", {"path": "/x.flp", "genre": "house", "roles": {"4": "lead"}}, runtime=rt
    )
    assert env["ok"] is True
    assert "lead" in env["result"]["metrics"]["roles"]


@pytest.mark.parametrize(
    "args",
    [
        {"path": "/x.flp", "genre": "polka"},
        {"path": "/x.flp"},
        {"path": "/x.flp", "genre": "house", "key": 7},
        {"path": "/x.flp", "genre": "house", "key": "C blues"},
        {"path": "/x.flp", "genre": "house", "roles": {"x": "bass"}},
        {"path": "/x.flp", "genre": "house", "roles": {"1": "tuba"}},
    ],
)
def test_invalid_args(args: dict) -> None:
    rt = FakeOfflineRuntime(response=BridgeResponse(ok=True, kind="describe", result=good_house()))
    env = offline_tool.execute("check_beat", args, runtime=rt)
    assert env["ok"] is False
    assert env["result"]["error"] == "INVALID_ARGS"


def test_bridge_error_propagates_as_check_beat() -> None:
    rt = FakeOfflineRuntime(
        response=BridgeResponse(ok=False, kind="describe", error="FILE_NOT_FOUND", message="nope")
    )
    env = offline_tool.execute("check_beat", {"path": "/x.flp", "genre": "house"}, runtime=rt)
    assert env["ok"] is False and env["kind"] == "check_beat"
    assert env["result"]["error"] == "INVALID_ARGS"


def test_check_beat_is_listed_and_read_only() -> None:
    assert "check_beat" in offline_tool.SUPPORTED_KINDS
    assert "check_beat" not in offline_tool.WRITE_KINDS
