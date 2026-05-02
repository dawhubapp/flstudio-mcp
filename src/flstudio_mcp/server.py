"""MCP server entrypoint — FastMCP on stdio."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from . import __version__
from .installer import midi_script as midi_script_installer
from .logging_setup import configure_logging, env_log_level, get_logger
from .resources import logs as logs_resource
from .runtime.live import LiveRuntime, default_runtime
from .tools import live as live_tool

NO_AUTO_INSTALL_ENV = "FLSTUDIO_MCP_NO_AUTO_INSTALL"
_LOG = get_logger("server")

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


def auto_install_midi_script() -> midi_script_installer.InstallResult | None:
    """Run the MIDI-script installer; return result, or ``None`` if disabled.

    Disabled when ``FLSTUDIO_MCP_NO_AUTO_INSTALL`` is set (per risk R8 in
    MCP-SPEC.md). On non-NOOP outcomes, prints a one-line stderr notice
    so users see *what* changed without trawling the log file.
    """
    if os.environ.get(NO_AUTO_INSTALL_ENV):
        _LOG.info("MIDI script auto-install disabled by env var")
        return None

    try:
        result = midi_script_installer.install_midi_script()
    except Exception as exc:
        _LOG.exception("MIDI script auto-install failed", extra={"error": type(exc).__name__})
        print(f"flstudio-mcp: MIDI script install failed: {exc}", file=sys.stderr)
        return None

    if result.action != midi_script_installer.InstallAction.NOOP:
        print(
            f"flstudio-mcp: MIDI script {result.action.value} → {result.target_path}. "
            "Reload the device in FL Studio (Options → MIDI Settings → Refresh).",
            file=sys.stderr,
        )
    return result


def build_server(
    *,
    runtime_factory: Callable[[], LiveRuntime] = _default_runtime_factory,
    install_result: midi_script_installer.InstallResult | None = None,
) -> FastMCP:
    """Construct the FastMCP server with tools and resources registered."""
    server = FastMCP(
        name=SERVER_NAME,
        instructions=_compose_instructions(install_result),
    )
    logs_resource.register(server)
    live_tool.register(server, runtime_factory)
    return server


def _compose_instructions(
    install_result: midi_script_installer.InstallResult | None,
) -> str:
    base = SERVER_INSTRUCTIONS
    if install_result is None:
        return base
    if install_result.action == midi_script_installer.InstallAction.NOOP:
        return base
    if install_result.action == midi_script_installer.InstallAction.HARDWARE_DIR_MISSING:
        return (
            base + "\n\nWARNING: FL Studio's Hardware dir was not found. The MIDI "
            "script could not be installed. Verify FL Studio 25.x is "
            "installed before invoking live_execute."
        )
    return (
        base + f"\n\nNOTE: the bundled MIDI script was just {install_result.action.value} "
        f"into FL Studio. Reload the device in FL (Options → MIDI Settings → Refresh) "
        "before calling live_execute."
    )


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
    install_result = auto_install_midi_script()
    server = build_server(install_result=install_result)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
