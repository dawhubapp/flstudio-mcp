"""MCP server entrypoint. Phase 1.2 fills in the FastMCP server boot."""

from __future__ import annotations

import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint. Stub until Phase 1.2.1."""
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        print(f"flstudio-mcp {__version__}")
        return 0
    print(
        "flstudio-mcp: server boot not yet implemented (Phase 1.2.1).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
