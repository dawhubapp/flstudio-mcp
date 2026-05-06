"""Unit tests for the multi-turn Claude API agent loop.

Uses ``ScriptedResponder`` + a fake MCP session so no real Anthropic
calls are made. Verifies the wiring details that would silently
misbehave: tool dispatch, tool_result feedback, iteration cap,
token cap, exception forwarding, tool filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ..harness.agent import (
    AgentConfig,
    FakeMessage,
    FakeTextBlock,
    FakeToolUseBlock,
    FakeUsage,
    ScriptedResponder,
    run_agent,
)

# ---------------------------------------------------------------- fake session #


@dataclass
class FakeHarnessSession:
    tools: list[dict[str, Any]] = field(default_factory=list)
    canned_envelopes: dict[str, dict[str, Any]] = field(default_factory=dict)
    raise_on: set[str] = field(default_factory=set)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def list_tools(self) -> list[dict[str, Any]]:
        return self.tools

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(args)))
        if name in self.raise_on:
            raise RuntimeError(f"injected failure for {name}")
        return self.canned_envelopes.get(
            name, {"ok": True, "kind": name, "result": {}, "duration_ms": 1.0, "log_id": "fake"}
        )


def _ok_envelope(kind: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "kind": kind, "result": result or {}, "duration_ms": 1.0, "log_id": "x"}


# ---------------------------------------------------------------- tests #


@pytest.mark.anyio
async def test_single_text_turn_end_turn() -> None:
    """Agent emits text + stops; no tools called."""
    session = FakeHarnessSession(tools=[])
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(content=[FakeTextBlock(text="all done")], stop_reason="end_turn"),
        ]
    )
    run = await run_agent(
        prompt="hi",
        session=session,
        cfg=AgentConfig(system_prompt="be brief", max_iterations=5),
        client=responder,
    )
    assert run.terminated == "end_turn"
    assert run.iterations == 1
    assert run.final_text == "all done"
    assert session.calls == []


@pytest.mark.anyio
async def test_tool_use_then_end_turn() -> None:
    """Tool call → tool result fed back → final text → end."""
    session = FakeHarnessSession(
        tools=[{"name": "offline_execute", "description": "", "input_schema": {}}],
        canned_envelopes={
            "offline_execute": _ok_envelope("describe", {"tempo": 120.0}),
        },
    )
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="call_1",
                        name="offline_execute",
                        input={"kind": "describe", "args": {"path": "/tmp/a.flp"}},
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeTextBlock(text="tempo is 120")], stop_reason="end_turn"),
        ]
    )
    run = await run_agent(
        prompt="what's the tempo?",
        session=session,
        cfg=AgentConfig(),
        client=responder,
    )
    assert run.terminated == "end_turn"
    assert run.iterations == 2
    assert session.calls == [
        ("offline_execute", {"kind": "describe", "args": {"path": "/tmp/a.flp"}}),
    ]
    # The 2nd messages.create must have received a tool_result block.
    second_call = responder.calls[1]
    last_user = second_call["messages"][-1]
    assert last_user["role"] == "user"
    [tr] = last_user["content"]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "call_1"


@pytest.mark.anyio
async def test_iteration_cap_hit_returns_partial_run() -> None:
    """Loop stops at max_iterations; AgentRun is still usable."""
    session = FakeHarnessSession(
        tools=[{"name": "offline_execute", "description": "", "input_schema": {}}],
    )

    # Always emit a tool_use, never end_turn — forces iter_cap.
    def _forever_tool(i: int) -> FakeMessage:
        return FakeMessage(
            content=[
                FakeToolUseBlock(id=f"c{i}", name="offline_execute", input={"kind": "list_apis"})
            ],
            stop_reason="tool_use",
        )

    responder = ScriptedResponder(messages_to_emit=[_forever_tool(i) for i in range(10)])
    run = await run_agent(
        prompt="loop forever",
        session=session,
        cfg=AgentConfig(max_iterations=3),
        client=responder,
    )
    assert run.terminated == "iter_cap"
    assert run.iterations == 3
    assert len(session.calls) == 3


@pytest.mark.anyio
async def test_token_cap_hit_stops_loop() -> None:
    """When cumulative input tokens exceed cap, stop with token_cap."""
    session = FakeHarnessSession(
        tools=[{"name": "offline_execute", "description": "", "input_schema": {}}],
    )
    # First turn returns big usage; loop should stop before second call.
    big_usage = FakeUsage(input_tokens=300_000, output_tokens=50)
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(id="c1", name="offline_execute", input={"kind": "list_apis"})
                ],
                stop_reason="tool_use",
                usage=big_usage,
            ),
            FakeMessage(content=[FakeTextBlock("done")], stop_reason="end_turn"),
        ]
    )
    run = await run_agent(
        prompt="x",
        session=session,
        cfg=AgentConfig(max_input_tokens=200_000),
        client=responder,
    )
    assert run.terminated == "token_cap"
    assert run.iterations == 1


@pytest.mark.anyio
async def test_tool_exception_becomes_error_envelope() -> None:
    """A raising session.call_tool yields ok=false to the model, doesn't crash."""
    session = FakeHarnessSession(
        tools=[{"name": "offline_execute", "description": "", "input_schema": {}}],
        raise_on={"offline_execute"},
    )
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(id="c1", name="offline_execute", input={"kind": "describe"})
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeTextBlock("recovered")], stop_reason="end_turn"),
        ]
    )
    run = await run_agent(
        prompt="x",
        session=session,
        cfg=AgentConfig(),
        client=responder,
    )
    assert run.terminated == "end_turn"
    assert run.turns[0].tool_calls[0]["ok"] is False
    # Tool result was forwarded as is_error=True
    second_call = responder.calls[1]
    [tr] = second_call["messages"][-1]["content"]
    assert tr["is_error"] is True


@pytest.mark.anyio
async def test_system_prompt_and_tool_caching_attached() -> None:
    """cache_system=True attaches cache_control to system + last tool."""
    session = FakeHarnessSession(
        tools=[
            {"name": "tool_a", "description": "", "input_schema": {}},
            {"name": "tool_b", "description": "", "input_schema": {}},
        ],
    )
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(content=[FakeTextBlock("ok")], stop_reason="end_turn"),
        ]
    )
    await run_agent(
        prompt="x",
        session=session,
        cfg=AgentConfig(system_prompt="be terse", cache_system=True),
        client=responder,
    )
    call = responder.calls[0]
    [sys_block] = call["system"]
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    # Cache marker on the LAST tool only.
    tools = call["tools"]
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.anyio
async def test_tool_filter_shrinks_tool_list() -> None:
    """tool_filter callback can drop tools the agent shouldn't see."""
    session = FakeHarnessSession(
        tools=[
            {"name": "live_execute", "description": "", "input_schema": {}},
            {"name": "offline_execute", "description": "", "input_schema": {}},
        ],
    )
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(content=[FakeTextBlock("ok")], stop_reason="end_turn"),
        ]
    )
    await run_agent(
        prompt="x",
        session=session,
        cfg=AgentConfig(),
        client=responder,
        tool_filter=lambda tools: [t for t in tools if t["name"] == "offline_execute"],
    )
    call = responder.calls[0]
    names = [t["name"] for t in call["tools"]]
    assert names == ["offline_execute"]


@pytest.mark.anyio
async def test_run_aggregates_token_totals() -> None:
    """AgentRun.total_input_tokens sums every turn."""
    session = FakeHarnessSession(tools=[])
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[FakeTextBlock("done")],
                stop_reason="end_turn",
                usage=FakeUsage(input_tokens=1234, output_tokens=56, cache_read_input_tokens=200),
            ),
        ]
    )
    run = await run_agent(
        prompt="x",
        session=session,
        cfg=AgentConfig(),
        client=responder,
    )
    assert run.total_input_tokens == 1234
    assert run.total_output_tokens == 56
    assert run.total_cache_read == 200
