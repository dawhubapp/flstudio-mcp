"""``logs://`` MCP resource — returns the most recent server log lines."""

from __future__ import annotations

import json
from collections.abc import Mapping

from mcp.server.fastmcp import FastMCP

from ..logging_setup import read_recent_log_lines

DEFAULT_TAIL = 50
MAX_TAIL = 500
RESOURCE_URI = "logs://recent"


def render_logs_payload(n: int = DEFAULT_TAIL) -> str:
    """Return a JSON array of the last ``n`` log entries (capped at MAX_TAIL)."""
    n = max(0, min(n, MAX_TAIL))
    entries: list[Mapping[str, object]] = read_recent_log_lines(n)
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))


def register(server: FastMCP) -> None:
    """Register the ``logs://recent`` resource on the given server."""

    @server.resource(
        RESOURCE_URI,
        name="recent_logs",
        description=(
            f"Last {DEFAULT_TAIL} structured log entries from the MCP server "
            "(JSON array). Each entry has `ts`, `level`, `logger`, `msg`, plus "
            "any extras attached at log time (kind, ok, duration_ms, …)."
        ),
        mime_type="application/json",
    )
    def _recent_logs() -> str:
        return render_logs_payload()
