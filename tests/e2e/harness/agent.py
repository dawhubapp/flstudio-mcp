"""Multi-turn Claude API agent loop.

Drives a Claude tool-use conversation where each ``tool_use`` block in
the model's response is dispatched to the MCP harness session and its
result fed back as a ``tool_result`` turn.

Design notes:

  * We translate MCP ``Tool.inputSchema`` (already JSON Schema) directly
    into Anthropic's ``tools=[]`` block — no shape munging.
  * Prompt caching: the system prompt + the entire tool list are
    invariant across turns, so we attach ``cache_control={"type":
    "ephemeral"}`` to the last tool. That caches everything before
    it, dropping per-iteration cost ~70%.
  * Iteration cap (default 30) and total input-token cap (default
    200_000) are enforced inside the loop. Both are *controlled*
    failure modes — the run still returns an ``AgentRun`` with
    ``terminated`` set, so the invariant validator gets a chance to
    score whatever state was reached (partial credit).
  * The Anthropic client is injected as a Protocol so the unit tests
    can swap in a ``ScriptedResponder`` and exercise the loop with
    zero token spend.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .client import HarnessSession

TerminationReason = Literal[
    "end_turn", "iter_cap", "token_cap", "exception", "max_tokens", "refusal"
]
DEFAULT_MODEL = "claude-opus-4-7"


class _Usage(Protocol):
    """Subset of anthropic.types.Usage we consume."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


class _Message(Protocol):
    """Subset of anthropic.types.Message we consume."""

    content: list[Any]  # TextBlock | ToolUseBlock — duck-typed below
    stop_reason: str | None
    usage: _Usage


class MessagesAPI(Protocol):
    async def create(self, **kwargs: Any) -> _Message: ...


class AnthropicClient(Protocol):
    messages: MessagesAPI


@dataclass
class AgentConfig:
    """Knobs for one ``run_agent`` invocation."""

    model: str = DEFAULT_MODEL
    system_prompt: str = ""
    max_iterations: int = 30
    max_input_tokens: int = 200_000
    max_output_tokens_per_turn: int = 4096
    cache_system: bool = True
    # Claude Opus 5.5 defaults to effort "medium"; brief runs set it explicitly.
    effort: str | None = None


@dataclass
class AgentTurn:
    """One iteration's input/output, captured for the artifact dump."""

    iteration: int
    stop_reason: str | None
    text_blocks: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class AgentRun:
    """Result of one full agent loop."""

    turns: list[AgentTurn] = field(default_factory=list)
    terminated: TerminationReason = "end_turn"
    elapsed_s: float = 0.0
    final_text: str = ""

    @property
    def iterations(self) -> int:
        return len(self.turns)

    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_cache_creation(self) -> int:
        return sum(t.cache_creation_tokens for t in self.turns)

    @property
    def total_cache_read(self) -> int:
        return sum(t.cache_read_tokens for t in self.turns)


def _attach_cache_control(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool with ephemeral cache_control."""
    if not tools:
        return tools
    out = [dict(t) for t in tools]
    out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


def _block_kind(block: Any) -> str:
    """Duck-type the block's kind: 'text' | 'tool_use' | other."""
    return (
        getattr(block, "type", "") or block.get("type", "")
        if isinstance(block, dict)
        else getattr(block, "type", "")
    )


def _block_text(block: Any) -> str:
    return getattr(block, "text", "") if not isinstance(block, dict) else block.get("text", "")


def _tool_use_fields(block: Any) -> tuple[str, str, dict[str, Any]]:
    """Extract (id, name, input) from a tool_use block (dict or object)."""
    if isinstance(block, dict):
        return block.get("id", ""), block.get("name", ""), dict(block.get("input", {}))
    return getattr(block, "id", ""), getattr(block, "name", ""), dict(getattr(block, "input", {}))


def _content_block_to_dict(block: Any) -> dict[str, Any] | None:
    """Serialise an assistant content block into the dict shape the API
    accepts back as a message.

    Anthropic's API requires assistant messages with tool_use blocks to
    be appended back in dict form; we normalise here.
    """
    kind = _block_kind(block)
    if kind == "text":
        return {"type": "text", "text": _block_text(block)}
    if kind == "tool_use":
        tool_id, name, payload = _tool_use_fields(block)
        return {"type": "tool_use", "id": tool_id, "name": name, "input": payload}
    if isinstance(block, dict):
        return dict(block)
    return None


def _assistant_content(blocks: list[Any]) -> list[Any]:
    """The assistant turn as sent back to the API.

    Real SDK blocks (they have ``to_dict``) go back unchanged: Claude
    Opus 5.5 always thinks, and thinking blocks must be replayed exactly
    as produced. Scripted test blocks are normalised to dicts; unknown
    ones are dropped rather than sent as empty text (a 400).
    """
    out: list[Any] = []
    for block in blocks:
        if hasattr(block, "to_dict"):
            out.append(block)
            continue
        as_dict = _content_block_to_dict(block)
        if as_dict is not None:
            out.append(as_dict)
    return out


async def run_agent(
    prompt: str,
    *,
    session: HarnessSession,
    cfg: AgentConfig,
    client: AnthropicClient,
    tool_filter: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> AgentRun:
    """Drive a Claude tool-use conversation against the MCP session.

    ``prompt`` is the initial user message. ``cfg.system_prompt`` is
    pinned in the system slot. ``tool_filter`` may shrink the tool
    list (e.g. drop ``offline_execute`` for a live-only test).
    """
    tools = await session.list_tools()
    if tool_filter is not None:
        tools = tool_filter(tools)
    if cfg.cache_system:
        tools = _attach_cache_control(tools)

    system: list[dict[str, Any]] = []
    if cfg.system_prompt:
        sys_block: dict[str, Any] = {"type": "text", "text": cfg.system_prompt}
        if cfg.cache_system:
            sys_block["cache_control"] = {"type": "ephemeral"}
        system.append(sys_block)

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    run = AgentRun()
    started = time.monotonic()

    for iteration in range(1, cfg.max_iterations + 1):
        if run.total_input_tokens >= cfg.max_input_tokens:
            run.terminated = "token_cap"
            break

        extra: dict[str, Any] = {}
        if cfg.effort:
            extra["output_config"] = {"effort": cfg.effort}
        message = await client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_output_tokens_per_turn,
            system=system or None,  # type: ignore[arg-type]
            tools=tools,
            messages=messages,
            **extra,
        )

        usage = message.usage
        turn = AgentTurn(
            iteration=iteration,
            stop_reason=message.stop_reason,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_creation_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )

        # Append the assistant turn verbatim so subsequent calls
        # have the full conversation history.
        assistant_blocks = _assistant_content(list(message.content))
        messages.append({"role": "assistant", "content": assistant_blocks})

        tool_results: list[dict[str, Any]] = []
        for block in message.content:
            kind = _block_kind(block)
            if kind == "text":
                turn.text_blocks.append(_block_text(block))
            elif kind == "tool_use":
                tool_id, name, args = _tool_use_fields(block)
                envelope = await _safe_call_tool(session, name, args)
                turn.tool_calls.append(
                    {
                        "id": tool_id,
                        "name": name,
                        "args": args,
                        "ok": bool(envelope.get("ok")),
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": _envelope_to_text(envelope),
                        "is_error": not envelope.get("ok", False),
                    }
                )

        run.turns.append(turn)

        if message.stop_reason == "refusal":
            run.terminated = "refusal"
            run.final_text = "\n".join(turn.text_blocks)
            break
        if message.stop_reason == "end_turn":
            run.terminated = "end_turn"
            run.final_text = "\n".join(turn.text_blocks)
            break
        if message.stop_reason == "max_tokens":
            run.terminated = "max_tokens"
            break

        if not tool_results:
            # No tool calls and not end_turn — model hit a refusal or
            # a stop_reason we don't handle. Bail with the text.
            run.terminated = "end_turn"
            run.final_text = "\n".join(turn.text_blocks)
            break

        messages.append({"role": "user", "content": tool_results})
    else:
        run.terminated = "iter_cap"

    run.elapsed_s = round(time.monotonic() - started, 3)
    return run


async def _safe_call_tool(
    session: HarnessSession, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Wrap session.call_tool so a tool-side exception becomes an
    error envelope rather than crashing the whole run."""
    try:
        return await session.call_tool(name, args)
    except Exception as exc:
        return {
            "ok": False,
            "kind": name,
            "result": {"error": "harness_exception", "message": repr(exc)},
        }


def _envelope_to_text(envelope: dict[str, Any]) -> str:
    """Render an MCP envelope into the JSON text the model expects.

    We pass the full structured envelope back so the agent has all the
    context: ok, kind, result, duration_ms, log_id.
    """
    import json as _json

    return _json.dumps(envelope, ensure_ascii=False, default=str)


# Convenience helpers used by the unit test scripted responder.

ScriptedTurn = Callable[[list[dict[str, Any]]], _Message]


@dataclass
class ScriptedResponder:
    """Test double for ``AnthropicClient`` driven by a list of canned
    messages. Each entry in ``messages_to_emit`` is yielded on a
    successive ``messages.create`` call.

    Wraps the calls in a tiny ``messages`` namespace so the loop's
    ``client.messages.create(...)`` line works unchanged.
    """

    messages_to_emit: list[_Message]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = _ScriptedMessages(self)


@dataclass
class _ScriptedMessages:
    parent: ScriptedResponder

    async def create(self, **kwargs: Any) -> _Message:
        # Deep-copy the call shape so subsequent loop mutations of
        # `messages` don't corrupt the recorded call. (The harness
        # appends to the same list across iterations.)
        import copy as _copy

        self.parent.calls.append(_copy.deepcopy(kwargs))
        if not self.parent.messages_to_emit:
            raise AssertionError("ScriptedResponder ran out of canned messages")
        return self.parent.messages_to_emit.pop(0)


# Trivial doubles for content blocks + usage so unit tests don't need
# to depend on the real Anthropic SDK types.


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int | None = 0
    cache_read_input_tokens: int | None = 0


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)


# `Awaitable` import retained for forward compatibility with future
# coroutine-shaped tool dispatch hooks; silence "unused import" linters.
_ = Awaitable
