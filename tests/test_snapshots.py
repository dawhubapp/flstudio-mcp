"""Tests for the FLP snapshot store."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from flstudio_mcp import snapshots


def _write_flp(tmp_path: Path, name: str = "demo.flp", contents: bytes = b"FLhd\x00\x06") -> Path:
    p = tmp_path / name
    p.write_bytes(contents)
    return p


def _store(tmp_path: Path, **kw) -> snapshots.SnapshotStore:
    return snapshots.SnapshotStore(root=tmp_path / "snaps", file_open_check=False, **kw)


# --------------------------------------------------------------------------- #
# snapshot()
# --------------------------------------------------------------------------- #


def test_snapshot_copies_file_and_writes_metadata(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = _store(tmp_path)

    meta = store.snapshot(flp, kind="set_tempo", command_id="cmd-1")

    snapshot_file = store._snapshot_file_path(meta.snapshot_id)
    meta_file = store._metadata_file_path(meta.snapshot_id)
    assert snapshot_file.read_bytes() == b"FLhd\x00\x06"
    parsed = json.loads(meta_file.read_text(encoding="utf-8"))
    assert parsed["kind"] == "set_tempo"
    assert parsed["command_id"] == "cmd-1"
    assert parsed["original_path"] == str(flp.resolve())
    assert parsed["sha256"] == meta.sha256
    assert parsed["size_bytes"] == 6


def test_snapshot_id_format(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path, "myproject.flp")
    store = _store(tmp_path)
    meta = store.snapshot(flp)
    slug, _, name = meta.snapshot_id.partition("/")
    assert slug == "myproject"
    assert "-" in name
    ts_part, hash_part = name.split("-", 1)
    assert len(ts_part) == 15  # 20260503T142217
    assert len(hash_part) == snapshots.HASH_PREFIX_LEN


def test_snapshot_missing_file_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(snapshots.SnapshotError, match="not found"):
        store.snapshot(tmp_path / "nope.flp")


def test_two_snapshots_of_same_file_get_distinct_ids(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = _store(tmp_path)
    a = store.snapshot(flp)
    time.sleep(1.05)  # ensure timestamp delta > 1s (TS resolution)
    b = store.snapshot(flp)
    assert a.snapshot_id != b.snapshot_id


def test_project_slug_strips_unsafe_chars(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path, "Weird Name!! v2.flp")
    store = _store(tmp_path)
    meta = store.snapshot(flp)
    assert meta.project_slug == "Weird-Name-v2"


# --------------------------------------------------------------------------- #
# restore()
# --------------------------------------------------------------------------- #


def test_restore_writes_back_to_original_path(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path, contents=b"original")
    store = _store(tmp_path)
    meta = store.snapshot(flp)
    flp.write_bytes(b"mutated")
    assert flp.read_bytes() == b"mutated"

    restored = store.restore(meta.snapshot_id)
    assert restored.snapshot_id == meta.snapshot_id
    assert flp.read_bytes() == b"original"


def test_restore_creates_parent_dir_if_missing(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path, contents=b"original")
    store = _store(tmp_path)
    meta = store.snapshot(flp)
    flp.unlink()

    store.restore(meta.snapshot_id)
    assert flp.read_bytes() == b"original"


def test_restore_unknown_id_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(snapshots.SnapshotNotFoundError):
        store.restore("ghost/20260101T000000-aaaaaa")


def test_restore_refuses_when_fl_has_file_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flp = _write_flp(tmp_path)
    store = snapshots.SnapshotStore(root=tmp_path / "snaps", file_open_check=True)
    meta = store.snapshot(flp)

    monkeypatch.setattr(snapshots, "_file_open_in_fl", lambda _p: True)
    with pytest.raises(snapshots.FileOpenInFLError, match="open"):
        store.restore(meta.snapshot_id)


def test_restore_proceeds_when_fl_does_not_have_file_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flp = _write_flp(tmp_path)
    store = snapshots.SnapshotStore(root=tmp_path / "snaps", file_open_check=True)
    meta = store.snapshot(flp)
    flp.write_bytes(b"changed")

    monkeypatch.setattr(snapshots, "_file_open_in_fl", lambda _p: False)
    store.restore(meta.snapshot_id)
    assert flp.read_bytes() == b"FLhd\x00\x06"


# --------------------------------------------------------------------------- #
# list_snapshots() + get()
# --------------------------------------------------------------------------- #


def test_list_returns_newest_first(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = _store(tmp_path)
    a = store.snapshot(flp)
    time.sleep(1.05)
    b = store.snapshot(flp)
    listed = store.list_snapshots()
    assert listed[0].snapshot_id == b.snapshot_id
    assert listed[1].snapshot_id == a.snapshot_id


def test_list_filtered_by_project(tmp_path: Path) -> None:
    a = _write_flp(tmp_path, "alpha.flp")
    b = _write_flp(tmp_path, "beta.flp")
    store = _store(tmp_path)
    store.snapshot(a)
    store.snapshot(b)
    only_alpha = store.list_snapshots(project_slug="alpha")
    assert len(only_alpha) == 1
    assert only_alpha[0].project_slug == "alpha"


def test_get_by_id(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = _store(tmp_path)
    meta = store.snapshot(flp)
    assert store.get(meta.snapshot_id) == meta


def test_get_unknown_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(snapshots.SnapshotNotFoundError):
        store.get("ghost/20260101T000000-aaaaaa")


def test_list_skips_corrupt_metadata(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = _store(tmp_path)
    meta = store.snapshot(flp)
    # Corrupt metadata file
    meta_path = store._metadata_file_path(meta.snapshot_id)
    meta_path.write_text("{not json", encoding="utf-8")
    listed = store.list_snapshots()
    assert listed == []


# --------------------------------------------------------------------------- #
# prune() + retention
# --------------------------------------------------------------------------- #


def test_prune_drops_oldest_beyond_retention(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = snapshots.SnapshotStore(
        root=tmp_path / "snaps", retention_per_project=3, file_open_check=False
    )
    metas = []
    for i in range(5):
        # Force distinct timestamps by manipulating the file
        flp.write_bytes(f"v{i}".encode())
        time.sleep(1.05)
        metas.append(store.snapshot(flp))

    remaining = store.list_snapshots(project_slug="demo")
    assert len(remaining) == 3
    # Newest 3 kept
    kept_ids = {m.snapshot_id for m in remaining}
    assert kept_ids == {m.snapshot_id for m in metas[-3:]}
    # Oldest 2 deleted
    for old in metas[:2]:
        assert not store._snapshot_file_path(old.snapshot_id).exists()
        assert not store._metadata_file_path(old.snapshot_id).exists()


def test_prune_under_retention_is_noop(tmp_path: Path) -> None:
    flp = _write_flp(tmp_path)
    store = snapshots.SnapshotStore(
        root=tmp_path / "snaps", retention_per_project=10, file_open_check=False
    )
    store.snapshot(flp)
    deleted = store.prune("demo")
    assert deleted == 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_metadata_to_from_dict_round_trip() -> None:
    meta = snapshots.SnapshotMetadata(
        snapshot_id="demo/20260101T000000-aaaaaa",
        project_slug="demo",
        original_path="/tmp/demo.flp",
        created_at=1234.5,
        sha256="a" * 64,
        size_bytes=42,
        kind="set_tempo",
        command_id="cmd-1",
        extra={"foo": "bar"},
    )
    assert snapshots.SnapshotMetadata.from_dict(meta.to_dict()) == meta


def test_malformed_id_raises_in_path_helpers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(snapshots.SnapshotError, match="malformed"):
        store._snapshot_file_path("no-slash")
