"""Snapshot store for FLP files (Phase 2.1).

Every mutating ``live_execute`` call snapshots the target FLP first so
the LLM can roll back any change. Snapshots live under
``~/Library/Application Support/flstudio-mcp/snapshots/<project>/`` —
one subdir per project, sidecar JSON per snapshot.

Layout::

    snapshots/
      <project_slug>/
        20260503T142217-a9f3c1.flp        # binary copy
        20260503T142217-a9f3c1.flp.json   # sidecar metadata
        20260503T143002-7e2b88.flp
        20260503T143002-7e2b88.flp.json

The snapshot id is ``<project_slug>/<ts>-<hash>`` — stable across the
project's lifetime, opaque to callers, and globally unique.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

DEFAULT_SNAPSHOT_DIR = (
    Path.home() / "Library" / "Application Support" / "flstudio-mcp" / "snapshots"
)
DEFAULT_RETENTION = 50
SNAPSHOT_SUFFIX = ".flp"
METADATA_SUFFIX = ".flp.json"
HASH_PREFIX_LEN = 6  # short hash inside snapshot id (full hash in metadata)
TS_FORMAT = "%Y%m%dT%H%M%S"

_LOG = get_logger("snapshots")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class SnapshotError(RuntimeError):
    """Raised when a snapshot operation fails for a reason worth surfacing."""


class SnapshotNotFoundError(SnapshotError):
    """Snapshot id has no corresponding file in the store."""


class FileOpenInFLError(SnapshotError):
    """Restore refused because FL Studio currently holds the file open."""


@dataclass(frozen=True)
class SnapshotMetadata:
    snapshot_id: str
    project_slug: str
    original_path: str
    created_at: float
    sha256: str
    size_bytes: int
    kind: str | None = None  # tool kind that produced this snapshot
    command_id: str | None = None  # IPC command id that triggered it
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotMetadata:
        return cls(
            snapshot_id=data["snapshot_id"],
            project_slug=data["project_slug"],
            original_path=data["original_path"],
            created_at=float(data["created_at"]),
            sha256=data["sha256"],
            size_bytes=int(data["size_bytes"]),
            kind=data.get("kind"),
            command_id=data.get("command_id"),
            extra=data.get("extra") or {},
        )


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", name).strip("-")
    return s or "unnamed"


def _project_slug_for(flp_path: Path) -> str:
    return _slugify(flp_path.stem)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_id(project_slug: str, ts: float, full_hash: str) -> str:
    ts_str = time.strftime(TS_FORMAT, time.gmtime(ts))
    return f"{project_slug}/{ts_str}-{full_hash[:HASH_PREFIX_LEN]}"


def _file_open_in_fl(path: Path) -> bool:
    """Best-effort check whether FL Studio (OsxFL) has ``path`` open via ``lsof``.

    Returns False on any uncertainty (lsof missing, permission denied,
    timeout) — restore semantics treat False as "go ahead" because the
    only real risk is FL re-saving over our restored bytes, which is
    obvious and recoverable.
    """
    try:
        proc = subprocess.run(
            ["lsof", "-Fpcn", str(path)],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0 or not proc.stdout:
        return False
    # lsof -F output: lines start with "p<pid>", "c<command>", "n<name>".
    for line in proc.stdout.splitlines():
        if line.startswith("c") and ("OsxFL" in line or "FL Studio" in line):
            return True
    return False


@dataclass
class SnapshotStore:
    """File-backed snapshot store rooted at ``root``."""

    root: Path = DEFAULT_SNAPSHOT_DIR
    retention_per_project: int = DEFAULT_RETENTION
    file_open_check: bool = True

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- snapshot ---------------------------------------------------------- #

    def snapshot(
        self,
        flp_path: Path,
        *,
        kind: str | None = None,
        command_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> SnapshotMetadata:
        """Copy ``flp_path`` into the store and return its metadata."""
        flp_path = Path(flp_path).resolve()
        if not flp_path.is_file():
            raise SnapshotError(f"flp file not found: {flp_path}")

        slug = _project_slug_for(flp_path)
        project_dir = self.root / slug
        project_dir.mkdir(parents=True, exist_ok=True)

        ts = time.time()
        sha = _file_sha256(flp_path)
        snapshot_id = _format_id(slug, ts, sha)
        target = self._snapshot_file_path(snapshot_id)
        meta_path = self._metadata_file_path(snapshot_id)

        shutil.copy2(flp_path, target)
        meta = SnapshotMetadata(
            snapshot_id=snapshot_id,
            project_slug=slug,
            original_path=str(flp_path),
            created_at=ts,
            sha256=sha,
            size_bytes=target.stat().st_size,
            kind=kind,
            command_id=command_id,
            extra=extra or {},
        )
        meta_path.write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        _LOG.info(
            "snapshot",
            extra={
                "snapshot_id": snapshot_id,
                "project_slug": slug,
                "size_bytes": meta.size_bytes,
                "kind": kind,
            },
        )

        self.prune(slug)
        return meta

    # --- restore ----------------------------------------------------------- #

    def restore(self, snapshot_id: str) -> SnapshotMetadata:
        """Restore the snapshot back to its original path.

        Refuses with :class:`FileOpenInFLError` if FL Studio currently
        holds the destination open (when ``file_open_check`` is True).
        """
        meta = self.get(snapshot_id)
        target = Path(meta.original_path)
        snapshot_file = self._snapshot_file_path(snapshot_id)

        if not snapshot_file.is_file():
            raise SnapshotNotFoundError(f"snapshot file missing on disk: {snapshot_file}")

        if self.file_open_check and target.exists() and _file_open_in_fl(target):
            raise FileOpenInFLError(
                f"FL Studio has {target} open — close the project before restoring"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_file, target)
        _LOG.info(
            "restore",
            extra={
                "snapshot_id": snapshot_id,
                "target": str(target),
                "size_bytes": meta.size_bytes,
            },
        )
        return meta

    # --- query ------------------------------------------------------------- #

    def get(self, snapshot_id: str) -> SnapshotMetadata:
        meta_path = self._metadata_file_path(snapshot_id)
        if not meta_path.is_file():
            raise SnapshotNotFoundError(f"unknown snapshot id: {snapshot_id}")
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"corrupt snapshot metadata at {meta_path}: {exc}") from exc
        return SnapshotMetadata.from_dict(raw)

    def list_snapshots(self, *, project_slug: str | None = None) -> list[SnapshotMetadata]:
        """Return all snapshot metadata, newest first.

        If ``project_slug`` is given, restrict to that project's subdir.
        """
        out: list[SnapshotMetadata] = []
        project_dirs: Sequence[Path]
        if project_slug is not None:
            project_dirs = [self.root / project_slug]
        else:
            project_dirs = sorted(p for p in self.root.iterdir() if p.is_dir())
        for project_dir in project_dirs:
            if not project_dir.is_dir():
                continue
            for meta_path in sorted(project_dir.glob(f"*{METADATA_SUFFIX}")):
                try:
                    raw = json.loads(meta_path.read_text(encoding="utf-8"))
                    out.append(SnapshotMetadata.from_dict(raw))
                except (json.JSONDecodeError, KeyError, ValueError):
                    _LOG.warning(
                        "skipping corrupt snapshot metadata",
                        extra={"path": str(meta_path)},
                    )
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    # --- retention --------------------------------------------------------- #

    def prune(self, project_slug: str) -> int:
        """Drop oldest snapshots beyond ``retention_per_project``.

        Returns the number deleted.
        """
        snaps = self.list_snapshots(project_slug=project_slug)
        excess = snaps[self.retention_per_project :]
        for meta in excess:
            self._delete(meta.snapshot_id)
        if excess:
            _LOG.info(
                "pruned snapshots",
                extra={
                    "project_slug": project_slug,
                    "deleted": len(excess),
                    "retained": len(snaps) - len(excess),
                },
            )
        return len(excess)

    def _delete(self, snapshot_id: str) -> None:
        for path in (
            self._snapshot_file_path(snapshot_id),
            self._metadata_file_path(snapshot_id),
        ):
            if path.exists():
                path.unlink()

    # --- path helpers ----------------------------------------------------- #

    def _snapshot_file_path(self, snapshot_id: str) -> Path:
        slug, _, name = snapshot_id.partition("/")
        if not name:
            raise SnapshotError(f"malformed snapshot id: {snapshot_id!r}")
        return self.root / slug / f"{name}{SNAPSHOT_SUFFIX}"

    def _metadata_file_path(self, snapshot_id: str) -> Path:
        slug, _, name = snapshot_id.partition("/")
        if not name:
            raise SnapshotError(f"malformed snapshot id: {snapshot_id!r}")
        return self.root / slug / f"{name}{METADATA_SUFFIX}"


def default_store() -> SnapshotStore:
    """Build a SnapshotStore at the default location."""
    return SnapshotStore()
