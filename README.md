# flstudio-mcp

MCP server for FL Studio. Lets Claude, Cursor, Codex CLI or any other
Model Context Protocol client read and edit FL Studio projects, either
live in a running FL Studio or offline from `.flp` files on disk.

> **Status: early preview (0.1.0.dev).** macOS + FL Studio 25/26.
> Tool names, arguments and install steps may still change. A tagged
> release is coming.

## What it does

Ask for project edits in plain language and the model calls the tools:

- *"What's the tempo of the project I have open, and how many channels and patterns does it have?"*
- *"Build a four-on-the-floor house pattern in the active pattern: kick on every beat, clap on 2 and 4, hats on the off-beats."*
- *"Crank the tempo up to 175 and rename channel 0 to STAB."* … *"Actually no, undo both."*

| MCP tool | What it does | Needs FL running |
|----------|--------------|------------------|
| `live_execute` | Talks to a running FL Studio over the IAC Driver and a bundled MIDI script: tempo, channels, mixer, patterns, step sequencer, plugin params, mixer EQ, save. Every write takes a snapshot first, so `restore_snapshot` can undo it. | yes (except setup kinds) |
| `offline_execute` | Reads and edits `.flp` files without FL: notes, controllers, patterns, channels, colors, routing, native plugin params, playlist clips, arrangements, whole-project reorganize. | no |
| `render_to_wav` | Renders a `.flp` song to WAV through FL and returns loudness, balance and silence metrics. | opens FL itself |

Full reference: [`docs/tools.md`](docs/tools.md). Example conversations:
[`docs/examples/`](docs/examples/).

## Install

Requirements: macOS, FL Studio 25.x or 26.x, Python 3.11+,
[uv](https://docs.astral.sh/uv/).

```sh
uvx --from git+https://github.com/dawhubapp/flstudio-mcp flstudio-mcp
```

Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "flstudio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/dawhubapp/flstudio-mcp", "flstudio-mcp"]
    }
  }
}
```

Installing the server isn't enough on its own. Live mode also needs:

1. The **IAC Driver** enabled in Audio MIDI Setup (one-time).
2. The auto-installed MIDI script wired inside FL Studio (Options →
   MIDI Settings → enable the IAC input → set Controller type to
   `flstudio-mcp`).

Offline mode needs the [`flpdiff`](https://github.com/dawhubapp/flpdiff)
bridge (bun + a flpdiff checkout for now).

Step-by-step for Claude Desktop, Cursor and Codex CLI:
**[`docs/install.md`](docs/install.md)**.

## Debugging

Use the MCP Inspector to call tools and resources from a browser UI:
**[`docs/debugging.md`](docs/debugging.md)**.

## Development

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## Related

- [`flpdiff`](https://github.com/dawhubapp/flpdiff): semantic diff and
  parser for FL Studio `.flp` files. flstudio-mcp's offline mode runs on
  its parser and serializer.

## License

MIT, see [LICENSE](LICENSE).
