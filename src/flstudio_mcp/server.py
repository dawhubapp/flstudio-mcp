"""MCP server entrypoint — FastMCP on stdio."""

from __future__ import annotations

import sys
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from . import __version__
from .logging_setup import configure_logging, env_log_level
from .resources import logs as logs_resource
from .runtime.live import LiveRuntime, default_runtime
from .tools import live as live_tool

SERVER_NAME = "flstudio-mcp"
SERVER_INSTRUCTIONS = (
    "FL Studio project introspection and mutation via MCP. "
    "Live mode talks to a running FL Studio 25.x instance over an IPC + "
    "MIDI-script harness. Offline mode reads/writes .flp files via the "
    "canonical TS parser from flpdiff invoked through Node. "
    "macOS only in v1."
)


def _default_runtime_factory() -> LiveRuntime:
    return default_runtime()


def build_server(
    *,
    runtime_factory: Callable[[], LiveRuntime] = _default_runtime_factory,
) -> FastMCP:
    """Construct the FastMCP server with tools and resources registered."""
    server = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
    )
    logs_resource.register(server)
    live_tool.register(server, runtime_factory)
    return server


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint. `--version` prints version, otherwise serve stdio."""
    args = sys.argv[1:] if argv is None else argv

    if "--version" in args:
        print(f"{SERVER_NAME} {__version__}")
        return 0

    if "--help" in args or "-h" in args:
        print(
            f"{SERVER_NAME} {__version__}\n"
            "\n"
            "Usage: flstudio-mcp [--version] [--help]\n"
            "\n"
            "With no flags, runs the MCP server on stdio. Designed to be\n"
            "invoked by an MCP client (Claude Desktop, Cursor, Codex CLI).\n"
        )
        return 0

    configure_logging(level=env_log_level())
    server = build_server()
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
