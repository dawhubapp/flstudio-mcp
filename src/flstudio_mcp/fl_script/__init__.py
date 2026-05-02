"""Bundled FL Studio MIDI script — deployed verbatim into FL's Hardware dir.

The contents of this directory run inside FL Studio's embedded Python
interpreter, **not** the MCP server's interpreter. Treat each file as
deployable data: don't import from it, don't lint-modify it, don't add
runtime-only Python features that FL's interpreter might lack.
"""
