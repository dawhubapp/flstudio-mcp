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

## Development

```sh
cd mcp
uv sync
uv run pytest
uv run ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
