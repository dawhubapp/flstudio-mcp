"""Tests for offline runtime — bridge command discovery + subprocess shape."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from flstudio_mcp.runtime import offline


def test_discover_bridge_cmd_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(offline.BRIDGE_ENV_VAR, "fake bun run /tmp/cli.ts bridge")
    assert offline.discover_bridge_cmd() == ["fake", "bun", "run", "/tmp/cli.ts", "bridge"]


def test_discover_bridge_cmd_via_dev_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(offline.BRIDGE_ENV_VAR, raising=False)
    cli_path = tmp_path / "flpdiff" / "src" / "cli.ts"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("// stub")
    monkeypatch.setattr(offline, "_walk_up_for_flpdiff", lambda _here: cli_path)
    monkeypatch.setattr(offline.shutil, "which", lambda b: "/fake/bun" if b == "bun" else None)
    cmd = offline.discover_bridge_cmd()
    assert cmd == ["/fake/bun", "run", str(cli_path), "bridge"]


def test_discover_bridge_cmd_via_path_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(offline.BRIDGE_ENV_VAR, raising=False)
    monkeypatch.setattr(offline, "_walk_up_for_flpdiff", lambda _here: None)
    monkeypatch.setattr(
        offline.shutil, "which", lambda b: "/usr/local/bin/flpdiff" if b == "flpdiff" else None
    )
    assert offline.discover_bridge_cmd() == ["/usr/local/bin/flpdiff", "bridge"]


def test_discover_bridge_cmd_raises_when_nothing_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(offline.BRIDGE_ENV_VAR, raising=False)
    monkeypatch.setattr(offline, "_walk_up_for_flpdiff", lambda _here: None)
    monkeypatch.setattr(offline.shutil, "which", lambda _b: None)
    with pytest.raises(offline.NodeNotFoundError):
        offline.discover_bridge_cmd()


def test_check_bridge_available_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(offline.BRIDGE_ENV_VAR, "x y z")
    ok, detail = offline.check_bridge_available()
    assert ok is True
    assert "x y z" in detail


def test_check_bridge_available_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(offline.BRIDGE_ENV_VAR, raising=False)
    monkeypatch.setattr(offline, "_walk_up_for_flpdiff", lambda _here: None)
    monkeypatch.setattr(offline.shutil, "which", lambda _b: None)
    ok, detail = offline.check_bridge_available()
    assert ok is False
    assert "no flpdiff bridge" in detail


@pytest.fixture
def fake_bridge(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub subprocess.run to capture input + return canned JSON."""
    state: dict = {"input": None, "stdout": '{"ok":true,"kind":"x","result":{"foo":"bar"}}'}

    def fake_run(cmd, *, input, capture_output, text, timeout, check):
        state["input"] = input
        state["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, state["stdout"], "")

    monkeypatch.setattr(offline.subprocess, "run", fake_run)
    return state


def test_offline_runtime_call_round_trip(fake_bridge: dict) -> None:
    rt = offline.OfflineRuntime(cmd=["fake-bridge"])
    resp = rt.call("describe", {"path": "/tmp/x.flp"})
    assert resp.ok is True
    assert resp.kind == "x"
    assert resp.result == {"foo": "bar"}
    assert json.loads(fake_bridge["input"]) == {
        "kind": "describe",
        "args": {"path": "/tmp/x.flp"},
    }


def test_offline_runtime_call_propagates_bridge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = '{"ok":false,"kind":"describe","error":"FILE_NOT_FOUND","message":"nope"}'
    monkeypatch.setattr(
        offline.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, payload, ""),
    )
    rt = offline.OfflineRuntime(cmd=["fake-bridge"])
    resp = rt.call("describe", {"path": "/no.flp"})
    assert resp.ok is False
    assert resp.error == "FILE_NOT_FOUND"
    assert resp.message == "nope"


def test_offline_runtime_call_subprocess_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        offline.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"),
    )
    rt = offline.OfflineRuntime(cmd=["fake-bridge"])
    with pytest.raises(offline.OfflineRuntimeError, match="exit=1"):
        rt.call("describe", {"path": "/x.flp"})


def test_offline_runtime_call_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=1)

    monkeypatch.setattr(offline.subprocess, "run", boom)
    rt = offline.OfflineRuntime(cmd=["fake-bridge"], timeout_s=1.0)
    with pytest.raises(offline.OfflineRuntimeError, match="timed out"):
        rt.call("describe", {"path": "/x.flp"})


def test_offline_runtime_call_empty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        offline.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "   \n", ""),
    )
    rt = offline.OfflineRuntime(cmd=["fake-bridge"])
    with pytest.raises(offline.OfflineRuntimeError, match="empty stdout"):
        rt.call("describe", {"path": "/x.flp"})


def test_offline_runtime_call_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        offline.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "not-json", ""),
    )
    rt = offline.OfflineRuntime(cmd=["fake-bridge"])
    with pytest.raises(offline.OfflineRuntimeError, match="not JSON"):
        rt.call("describe", {"path": "/x.flp"})


def test_offline_runtime_call_command_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError("/no/such/binary")

    monkeypatch.setattr(offline.subprocess, "run", boom)
    rt = offline.OfflineRuntime(cmd=["/no/such/binary", "bridge"])
    with pytest.raises(offline.NodeNotFoundError, match="not executable"):
        rt.call("describe", {"path": "/x.flp"})
