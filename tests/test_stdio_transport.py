"""End-to-end MCP stdio transport tests.

Spawns flstudio-mcp as a subprocess, opens a real MCP client session,
and exercises tools through the actual JSON-RPC protocol — same path
Claude Desktop / Cursor / Inspector use.

These tests are not gated behind FLSTUDIO_MCP_INTEGRATION because they
don't touch FL Studio. They DO need:
  * macOS (skipped elsewhere) — IAC plugin path is darwin-only
  * CoreMIDI available — skipped if ctypes can't load it

Server is launched with FLSTUDIO_MCP_NO_AUTO_INSTALL=1 +
FLSTUDIO_MCP_HARDWARE_DIR pointed at a tmp dir so it never touches the
real FL Hardware folder.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from flstudio_mcp.installer import iac
from flstudio_mcp.tools import live as live_tool

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="macOS only"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _server_params(tmp_path: Path, *, log_level: str = "WARNING") -> StdioServerParameters:
    """Launch via the installed entry point with isolated state dirs."""
    env = {
        **os.environ,
        "FLSTUDIO_MCP_NO_AUTO_INSTALL": "1",
        "FLSTUDIO_MCP_HARDWARE_DIR": str(tmp_path / "Hardware"),
        "FLSTUDIO_MCP_LOG_LEVEL": log_level,
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "flstudio_mcp.server"],
        env=env,
    )


def _structured(call_result) -> dict:
    """Pull the structured payload out of a call_tool response."""
    if call_result.structuredContent is not None:
        return call_result.structuredContent
    # Fallback: parse first text block.
    for block in call_result.content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no structured content in {call_result!r}")


@pytest.mark.anyio
async def test_handshake_and_tool_listing(tmp_path: Path) -> None:
    async with (
        stdio_client(_server_params(tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.serverInfo.name == "flstudio-mcp"

        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert live_tool.TOOL_NAME in names


@pytest.mark.anyio
async def test_list_apis_via_stdio(tmp_path: Path) -> None:
    async with (
        stdio_client(_server_params(tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(live_tool.TOOL_NAME, {"kind": "list_apis"})
        payload = _structured(result)
        assert payload["ok"] is True
        assert payload["kind"] == "list_apis"
        assert "check_iac" in payload["result"]["kinds"]


@pytest.mark.anyio
async def test_check_iac_via_stdio_matches_local_probe(tmp_path: Path) -> None:
    """check_iac through the wire matches a direct in-process probe.

    Confirms the CoreMIDI ctypes detector survives the full stdio
    round-trip (subprocess → JSON-RPC → tool dispatch → envelope →
    JSON-RPC → client). If this passes locally, the Inspector will see
    the same result.
    """
    if iac._coremidi_endpoint_counts() is None:
        pytest.skip("CoreMIDI not reachable on this host")

    expected = iac.check_iac_status()

    async with (
        stdio_client(_server_params(tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(live_tool.TOOL_NAME, {"kind": "check_iac"})
        payload = _structured(result)

    assert payload["ok"] is True
    assert payload["kind"] == "check_iac"
    assert payload["result"]["state"] == expected.state.value
    assert payload["result"]["ok"] == expected.ok


@pytest.mark.anyio
async def test_logs_resource_via_stdio(tmp_path: Path) -> None:
    """logs:// resource accessible over the wire after a tool call."""
    # The live_execute begin/ok + telemetry entries are logged at INFO;
    # the shared WARNING default would suppress them and the assertion
    # below would only pass on stale entries from earlier dev sessions.
    async with (
        stdio_client(_server_params(tmp_path, log_level="INFO")) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await session.call_tool(live_tool.TOOL_NAME, {"kind": "list_apis"})

        contents = await session.read_resource("logs://recent")
        assert contents.contents, "logs:// returned no content"
        text = contents.contents[0].text  # type: ignore[union-attr]
        entries = json.loads(text)
        assert isinstance(entries, list)
        assert any(e.get("kind") == "live_execute.list_apis" for e in entries)
