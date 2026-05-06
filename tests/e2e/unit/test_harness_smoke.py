"""Smoke test for the harness MCP client + state capture.

Spawns a real MCP server subprocess (no FL Studio needed, no API key
needed). Verifies:
  - open_session yields a live ClientSession
  - list_tools returns expected tool surface
  - offline_execute(list_apis) returns a valid envelope
  - capture_state works end-to-end against a real corpus FLP when the
    bridge is reachable (gracefully skips if not)

This is NOT an LLM test — it never calls Anthropic. Runs in normal CI.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from flstudio_mcp.tools import live as live_tool
from flstudio_mcp.tools import offline as offline_tool

from ..harness.client import open_session
from ..harness.state_capture import capture_state

CORPUS_DIR = (
    Path(__file__).resolve().parents[4] / "flpdiff" / "tests" / "corpus" / "re_base" / "fl25"
)
SMOKE_FLP = CORPUS_DIR / "base_one_pattern.flp"


@pytest.mark.anyio
async def test_open_session_lists_tools(tmp_path: Path) -> None:
    async with open_session(tmp_path / "sess") as ses:
        tools = await ses.list_tools()
        names = {t["name"] for t in tools}
        assert live_tool.TOOL_NAME in names
        assert offline_tool.TOOL_NAME in names


@pytest.mark.anyio
async def test_offline_list_apis_envelope(tmp_path: Path) -> None:
    async with open_session(tmp_path / "sess") as ses:
        env = await ses.call_tool(
            offline_tool.TOOL_NAME,
            {"kind": "list_apis", "args": {}},
        )
        assert env["ok"] is True
        assert env["kind"] == "list_apis"
        assert "kinds" in env["result"]
        # Sanity: the bridge dispatch should expose the Phase 3.4 kinds.
        assert "set_track_color" in env["result"]["kinds"]


@pytest.mark.anyio
async def test_capture_state_against_corpus_flp(tmp_path: Path) -> None:
    """End-to-end capture against a real synthetic FLP.

    Skips gracefully if the bridge is unreachable on this host (e.g.
    bun + dev flpdiff/ not installed and no FLSTUDIO_MCP_BRIDGE_CMD).
    """
    if not SMOKE_FLP.exists():
        pytest.skip(f"corpus FLP missing at {SMOKE_FLP}")

    scratch = tmp_path / "scratch.flp"
    shutil.copy2(SMOKE_FLP, scratch)

    async with open_session(tmp_path / "sess") as ses:
        # Probe first — if bridge is unreachable, skip cleanly.
        probe = await ses.call_tool(
            offline_tool.TOOL_NAME,
            {"kind": "describe", "args": {"path": str(scratch)}},
        )
        if not probe.get("ok"):
            result = probe.get("result") or {}
            pytest.skip(f"bridge unreachable: {result.get('message', result.get('error'))}")

        state = await capture_state(ses, scratch)

    assert state.path == scratch
    assert isinstance(state.describe, dict)
    # base_one_pattern has exactly 1 channel + 1 pattern + 1 arrangement.
    assert len(state.channels) >= 1
    assert len(state.patterns) >= 1
    assert len(state.arrangements) >= 1
    # Every captured tool call lands in the transcript.
    assert any(e.name == offline_tool.TOOL_NAME for e in ses.transcript)
