"""Tests for the ``snapshots://recent`` MCP resource."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import AnyUrl

from flstudio_mcp import server, snapshots
from flstudio_mcp.resources import snapshots as snap_resource


@pytest.fixture
def store(tmp_path: Path) -> snapshots.SnapshotStore:
    return snapshots.SnapshotStore(root=tmp_path / "store", file_open_check=False)


def _seed(store: snapshots.SnapshotStore, tmp_path: Path) -> snapshots.SnapshotMetadata:
    flp = tmp_path / "demo.flp"
    flp.write_bytes(b"FLhd")
    return store.snapshot(flp, kind="set_tempo")


def test_render_returns_json_array(store: snapshots.SnapshotStore, tmp_path: Path) -> None:
    _seed(store, tmp_path)
    payload = snap_resource.render_snapshots_payload(store)
    decoded = json.loads(payload)
    assert isinstance(decoded, list)
    assert len(decoded) == 1
    assert decoded[0]["kind"] == "set_tempo"


def test_render_caps_limit(store: snapshots.SnapshotStore) -> None:
    payload = snap_resource.render_snapshots_payload(store, limit=10_000)
    assert isinstance(json.loads(payload), list)


def test_render_negative_limit(store: snapshots.SnapshotStore) -> None:
    assert json.loads(snap_resource.render_snapshots_payload(store, limit=-5)) == []


def test_resource_registered_on_server(store: snapshots.SnapshotStore) -> None:
    instance = server.build_server()
    assert any(
        getattr(r, "uri", None) == AnyUrl(snap_resource.RESOURCE_URI)
        for r in instance._resource_manager.list_resources()
    )
