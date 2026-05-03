# flstudio-mcp

Model Context Protocol (MCP) server exposing FL Studio project introspection and mutation as LLM tools.

> **Status:** v0.1 in development. Spec: [`MCP-SPEC.md`](../MCP-SPEC.md) (in dawhub workspace).

## Overview

`flstudio-mcp` bridges LLM-driven workflows (Claude Desktop, Cursor, Codex CLI) and FL Studio projects via two execution modes:

- **Live** — commands sent to a running FL Studio 25.x instance over an IPC + MIDI-script harness.
- **Offline** — `.flp` files read/written without FL, via the canonical TS parser from [`flpdiff`](https://github.com/pronskiy/flpdiff) invoked through Node.

Primary use case: AI-assisted music production. Producer chats with an MCP client, asks for project edits in natural language ("set tempo to 128", "rename channel 3 to Kick", "tune the EQ on the bass insert").

## Platform support (v1)

- macOS only
- FL Studio 25.x only
- Python 3.11+
- Node 18+ (for offline runtime)

## Install

```sh
uvx --from git+https://github.com/<org>/flstudio-mcp flstudio-mcp
```

(Org TBD — see Phase 1.1.4 in the spec.)

Server install ≠ ready to use. You also need to:

1. Enable the **IAC Driver** in Audio MIDI Setup (one-time).
2. Wire the auto-installed MIDI script inside FL Studio (Options →
   MIDI Settings → enable IAC input → set Controller type to
   `flstudio-mcp`).

Full step-by-step: **[`docs/install.md`](docs/install.md)**.

## Debugging

Use the MCP Inspector to call tools / resources directly from a browser
UI: **[`docs/debugging.md`](docs/debugging.md)**.

## Development

```sh
cd mcp
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
