"""Tests for the live runtime — uses an in-memory fake inbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from flstudio_mcp.runtime import live


@dataclass
class _FakeCommand:
    id: str
    kind: str
    args: dict[str, Any]


@dataclass
class _FakeResult:
    id: str
    status: str = "ok"
    detail: str = ""
    artifact_path: str | None = None


@dataclass
class FakeInbox:
    written: list[_FakeCommand] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    next_status: str = "ok"
    next_detail: str = ""
    raise_on_wait: Exception | None = None

    def write_command(self, cmd: _FakeCommand) -> None:
        self.written.append(cmd)

    def wait_for_result(
        self, cmd_id: str, *, timeout: float = 30.0, poll_interval: float = 0.1
    ) -> _FakeResult:
        if self.raise_on_wait is not None:
            raise self.raise_on_wait
        return _FakeResult(id=cmd_id, status=self.next_status, detail=self.next_detail)

    def archive(self, cmd_id: str) -> None:
        self.archived.append(cmd_id)


@pytest.fixture
def runtime() -> tuple[live.LiveRuntime, FakeInbox]:
    inbox = FakeInbox()
    rt = live.LiveRuntime(inbox=inbox, _command_cls=_FakeCommand)
    return rt, inbox


def test_send_writes_command_and_returns_result(
    runtime: tuple[live.LiveRuntime, FakeInbox],
) -> None:
    rt, inbox = runtime
    inbox.next_detail = "{}"
    result = rt.send("describe", {"foo": "bar"})
    assert len(inbox.written) == 1
    cmd = inbox.written[0]
    assert cmd.kind == "describe"
    assert cmd.args == {"foo": "bar"}
    assert result.id == cmd.id
    assert result.status == "ok"


def test_send_archives_after(runtime: tuple[live.LiveRuntime, FakeInbox]) -> None:
    rt, inbox = runtime
    rt.send("describe")
    assert inbox.archived == [inbox.written[0].id]


def test_send_archives_even_on_timeout(
    runtime: tuple[live.LiveRuntime, FakeInbox],
) -> None:
    rt, inbox = runtime
    inbox.raise_on_wait = TimeoutError("boom")
    with pytest.raises(TimeoutError):
        rt.send("describe")
    assert inbox.archived == [inbox.written[0].id]


def test_archive_after_disabled(runtime: tuple[live.LiveRuntime, FakeInbox]) -> None:
    rt, inbox = runtime
    rt.archive_after = False
    rt.send("describe")
    assert inbox.archived == []


def test_make_command_id_unique() -> None:
    ids = {live.make_command_id() for _ in range(100)}
    assert len(ids) == 100


def test_noop_uses_noop_kind(runtime: tuple[live.LiveRuntime, FakeInbox]) -> None:
    rt, inbox = runtime
    rt.noop()
    assert inbox.written[0].kind == "noop"


def test_send_per_call_timeout_override(
    runtime: tuple[live.LiveRuntime, FakeInbox], monkeypatch: pytest.MonkeyPatch
) -> None:
    rt, inbox = runtime
    captured: dict[str, float] = {}

    real_wait = inbox.wait_for_result

    def spy(cmd_id: str, *, timeout: float = 30.0, poll_interval: float = 0.1):
        captured["timeout"] = timeout
        return real_wait(cmd_id, timeout=timeout, poll_interval=poll_interval)

    inbox.wait_for_result = spy  # type: ignore[assignment]
    rt.send("describe", timeout_s=0.5)
    assert captured["timeout"] == 0.5
