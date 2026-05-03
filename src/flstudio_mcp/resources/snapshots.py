"""``snapshots://`` MCP resource — lists all FLP snapshots."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from ..snapshots import SnapshotStore, default_store

RESOURCE_URI = "snapshots://recent"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def render_snapshots_payload(store: SnapshotStore | None = None, limit: int = DEFAULT_LIMIT) -> str:
    """Return a JSON array of snapshot metadata, newest first."""
    limit = max(0, min(limit, MAX_LIMIT))
    store = store or default_store()
    metas = store.list_snapshots()[:limit]
    return json.dumps([m.to_dict() for m in metas], ensure_ascii=False, separators=(",", ":"))


def register(server: FastMCP, store: SnapshotStore | None = None) -> None:
    """Register the ``snapshots://recent`` resource."""

    @server.resource(
        RESOURCE_URI,
        name="recent_snapshots",
        description=(
            "JSON array of every FLP snapshot in the local store, newest first. "
            "Each entry has snapshot_id, project_slug, original_path, created_at, "
            "sha256, size_bytes, and the kind/command_id that produced it. Use "
            'live_execute(kind="restore_snapshot", args={"snapshot_id": ...}) '
            "to roll back."
        ),
        mime_type="application/json",
    )
    def _recent_snapshots() -> str:
        return render_snapshots_payload(store)
