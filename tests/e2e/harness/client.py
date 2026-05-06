"""MCP stdio client wrapper for the e2e harness.

Spawns ``python -m flstudio_mcp.server`` (the same path Claude Desktop
uses) and gives the harness an ergonomic async session that records
every tool call into a transcript.

Mirrors the working pattern in ``tests/test_stdio_transport.py`` —
same ``StdioServerParameters``, same env-isolation discipline (no
auto-install, no real Hardware dir), same structured-payload extractor.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@dataclass
class TranscriptEntry:
    """One tool call + result, recorded for forensic review."""

    name: str
    args: dict[str, Any]
    structured: dict[str, Any] | None
    is_error: bool
    latency_ms: float


@dataclass
class HarnessSession:
    """Wraps a live ``ClientSession`` with transcript bookkeeping."""

    session: ClientSession
    scratch_dir: Path
    transcript: list[TranscriptEntry] = field(default_factory=list)
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Cache and return tool schemas in Anthropic-compatible shape."""
        if self.tool_schemas:
            return self.tool_schemas
        result = await self.session.list_tools()
        self.tool_schemas = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]
        return self.tool_schemas

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call ``name(args)``, parse structured payload, record transcript."""
        start = time.monotonic()
        result = await self.session.call_tool(name, args)
        latency_ms = (time.monotonic() - start) * 1000.0
        structured = _extract_structured(result)
        self.transcript.append(
            TranscriptEntry(
                name=name,
                args=dict(args),
                structured=structured,
                is_error=bool(result.isError),
                latency_ms=round(latency_ms, 2),
            )
        )
        if structured is None:
            raise RuntimeError(f"tool {name!r} returned no structured payload")
        return structured


def _extract_structured(call_result: Any) -> dict[str, Any] | None:
    """Pull the JSON envelope out of a CallToolResult."""
    if call_result.structuredContent is not None:
        return call_result.structuredContent
    for block in call_result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None
    return None


def _server_params(scratch_dir: Path, *, bridge_cmd: str | None) -> StdioServerParameters:
    """Build subprocess params with isolated state + dev bridge inheritance.

    ``FLSTUDIO_MCP_BRIDGE_CMD`` propagates from the parent env (or the
    explicit override) so the harness pins to the dev ``flpdiff/`` checkout
    rather than any globally-installed ``flpdiff``.
    """
    env = {
        **os.environ,
        "FLSTUDIO_MCP_NO_AUTO_INSTALL": "1",
        "FLSTUDIO_MCP_HARDWARE_DIR": str(scratch_dir / "Hardware"),
        "FLSTUDIO_MCP_LOG_LEVEL": "INFO",
    }
    resolved_bridge = (
        bridge_cmd if bridge_cmd is not None else os.environ.get("FLSTUDIO_MCP_BRIDGE_CMD")
    )
    if resolved_bridge:
        env["FLSTUDIO_MCP_BRIDGE_CMD"] = resolved_bridge
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "flstudio_mcp.server"],
        env=env,
    )


@asynccontextmanager
async def open_session(
    scratch_dir: Path,
    *,
    bridge_cmd: str | None = None,
) -> AsyncIterator[HarnessSession]:
    """Spawn the MCP server and yield a HarnessSession.

    Cleanup on exit closes the stdio streams + reaps the subprocess.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    params = _server_params(scratch_dir, bridge_cmd=bridge_cmd)
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield HarnessSession(session=session, scratch_dir=scratch_dir)
