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
from .resources import snapshots as snapshots_resource
from .runtime.live import LiveRuntime, default_runtime
from .tools import live as live_tool
from .tools import offline as offline_tool
from .tools import render as render_tool

NO_AUTO_INSTALL_ENV = "FLSTUDIO_MCP_NO_AUTO_INSTALL"
RE_HARNESS_INBOX_ENV = "FLPDIFF_HARNESS_INBOX"
_LOG = get_logger("server")


def _sync_runtime_env() -> None:
    """Point re_harness.ipc at the same runtime path the FL script uses.

    re_harness.ipc.DEFAULT_RUNTIME_ROOT historically pointed at
    `flpdiff-harness/runtime` (legacy from the flpdiff RE harness). The
    flstudio-mcp script now lives + writes under `flstudio-mcp/runtime`
    because FL's sandbox only allows writes inside the script's own
    Hardware subdir. Set the env var before re_harness imports cache
    the legacy default.
    """
    if RE_HARNESS_INBOX_ENV in os.environ:
        return  # explicit override wins
    target = midi_script_installer.runtime_root_for(midi_script_installer.hardware_dir())
    os.environ[RE_HARNESS_INBOX_ENV] = str(target)


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


def auto_install_midi_script() -> list[midi_script_installer.InstallResult] | None:
    """Run the MIDI-script installer; return list of results, or ``None`` if disabled.

    Disabled when ``FLSTUDIO_MCP_NO_AUTO_INSTALL`` is set (per risk R8 in
    MCP-SPEC.md). When ``FLSTUDIO_MCP_HARDWARE_DIR`` is set, installs
    only into that one path; otherwise installs into every detected
    ``FL Studio*/Settings/Hardware/`` to cover side-by-side FL versions.

    On non-NOOP outcomes, prints a one-line stderr notice per target
    so users see exactly which versions were touched.
    """
    if os.environ.get(NO_AUTO_INSTALL_ENV):
        _LOG.info("MIDI script auto-install disabled by env var")
        return None

    try:
        if os.environ.get("FLSTUDIO_MCP_HARDWARE_DIR"):
            results = [midi_script_installer.install_midi_script()]
        else:
            results = midi_script_installer.install_to_all_fl_versions()
    except Exception as exc:
        _LOG.exception("MIDI script auto-install failed", extra={"error": type(exc).__name__})
        print(f"flstudio-mcp: MIDI script install failed: {exc}", file=sys.stderr)
        return None

    for result in results:
        if result.action != midi_script_installer.InstallAction.NOOP:
            print(
                f"flstudio-mcp: MIDI script {result.action.value} → {result.target_path}. "
                "In FL Studio: Options → MIDI Settings → click 'Update MIDI scripts', "
                "then set the IAC Driver Bus 1 input's Controller type to 'flstudio-mcp'.",
                file=sys.stderr,
            )
    return results


def build_server(
    *,
    runtime_factory: Callable[[], LiveRuntime] = _default_runtime_factory,
    install_result: list[midi_script_installer.InstallResult] | None = None,
) -> FastMCP:
    """Construct the FastMCP server with tools and resources registered."""
    server = FastMCP(
        name=SERVER_NAME,
        instructions=_compose_instructions(install_result),
    )
    logs_resource.register(server)
    snapshots_resource.register(server)
    live_tool.register(server, runtime_factory)
    offline_tool.register(server)
    render_tool.register(server)
    return server


def _compose_instructions(
    install_results: list[midi_script_installer.InstallResult] | None,
) -> str:
    base = SERVER_INSTRUCTIONS
    if not install_results:
        return base
    actions = [r.action for r in install_results]
    if all(a == midi_script_installer.InstallAction.NOOP for a in actions):
        return base
    if all(a == midi_script_installer.InstallAction.HARDWARE_DIR_MISSING for a in actions):
        return (
            base + "\n\nWARNING: FL Studio's Hardware dir was not found. The MIDI "
            "script could not be installed. Verify FL Studio 25.x is "
            "installed before invoking live_execute."
        )
    touched = [r for r in install_results if r.action != midi_script_installer.InstallAction.NOOP]
    paths = "\n  ".join(str(r.target_path) for r in touched)
    return (
        base + f"\n\nNOTE: the bundled MIDI script was {touched[0].action.value} into "
        f"{len(touched)} FL Studio install(s):\n  {paths}\n"
        "In FL: Options → MIDI Settings → click 'Update MIDI scripts', "
        "then set the IAC Driver Bus 1 input's Controller type to 'flstudio-mcp' "
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
    _sync_runtime_env()
    install_result = auto_install_midi_script()
    server = build_server(install_result=install_result)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
