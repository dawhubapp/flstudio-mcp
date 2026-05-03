"""Auto-install the bundled FL Studio MIDI script.

Per Phase 1.4 of MCP-SPEC.md: detect FL's Hardware dir, hash-compare
the bundled script against the installed copy, install (symlink in
dev, copy in production) if missing or stale, drop a sidecar version
stamp.

Idempotent — running twice with no changes makes no writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

from .. import __version__
from ..logging_setup import get_logger

FL_USERDATA_PARENT = Path.home() / "Documents" / "Image-Line"
# Modern FL versions (2024+) share a single ~/Documents/Image-Line/FL Studio/
# user-data dir regardless of installed FL exe version. Older versions
# sometimes used ~/Documents/Image-Line/FL Studio 20/ / FL Studio 21/ etc.
# discover_fl_hardware_dirs() handles both shapes.
FL_USERDATA_GLOB = "FL Studio*"
SETTINGS_SUBPATH = Path("Settings") / "Hardware"
DEFAULT_FL_HARDWARE_DIR = FL_USERDATA_PARENT / "FL Studio" / SETTINGS_SUBPATH
# FL Studio scans `Settings/Hardware/<subdir>/device_*.py` — scripts at
# the Hardware/ root are NOT discovered. The subdir name doesn't have
# to match the script name but using the same name keeps things sane.
SCRIPT_SUBDIR = "flstudio-mcp"
SCRIPT_FILENAME = "device_flstudio_mcp.py"
VERSION_STAMP_SUFFIX = ".version.json"
PACKAGE_DATA = "flstudio_mcp.fl_script"

# Runtime IPC directory layout. MUST live inside the same Hardware
# subdir as the installed script — FL's sandbox only allows writes
# within the script's own directory tree. Anywhere else returns
# `<class '_io.FileIO'> returned NULL` even when the dir exists.
#
# Pre-created here because FL Studio's sandboxed Python also cannot
# makedirs fresh subdirs under ~/Documents/...
RUNTIME_SUBDIR = Path(SCRIPT_SUBDIR) / "runtime"
RUNTIME_LEAF_DIRS = ("inbox", "outbox", "processed")


def runtime_root_for(hardware_dir_path: Path) -> Path:
    """Absolute path of the IPC runtime root for a given Hardware dir."""
    return hardware_dir_path / RUNTIME_SUBDIR


_LOG = get_logger("installer.midi_script")


class InstallAction(StrEnum):
    """Outcome of a single install attempt."""

    NOOP = "noop"  # bundled hash matches installed
    INSTALLED = "installed"  # no prior copy
    UPDATED = "updated"  # bundled hash differs
    HARDWARE_DIR_MISSING = "hardware_dir_missing"


@dataclass(frozen=True)
class InstallResult:
    action: InstallAction
    target_path: Path
    bundled_hash: str
    installed_hash: str | None
    used_symlink: bool
    runtime_dirs: tuple[Path, ...] = ()

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "target_path": str(self.target_path),
            "bundled_hash": self.bundled_hash,
            "installed_hash": self.installed_hash,
            "used_symlink": self.used_symlink,
            "runtime_dirs": [str(p) for p in self.runtime_dirs],
        }


def bundled_script_path() -> Path:
    """Return the on-disk path of the bundled MIDI script."""
    return Path(str(resources.files(PACKAGE_DATA).joinpath(SCRIPT_FILENAME)))


def hardware_dir(override: Path | None = None) -> Path:
    """Return the FL Studio Hardware dir, with optional explicit override."""
    if override is not None:
        return Path(override)
    env = os.environ.get("FLSTUDIO_MCP_HARDWARE_DIR")
    if env:
        return Path(env).expanduser()
    return DEFAULT_FL_HARDWARE_DIR


def discover_fl_hardware_dirs(parent: Path | None = None) -> list[Path]:
    """Return every existing ``FL Studio*/Settings/Hardware/`` directory.

    Catches both the modern shared layout (single ``FL Studio/``) and
    legacy per-version layouts (``FL Studio 20/``, ``FL Studio 21/``,
    ``FL Studio 2024/``, …).
    """
    root = parent or FL_USERDATA_PARENT
    if not root.exists():
        return []
    found: list[Path] = []
    for child in sorted(root.glob(FL_USERDATA_GLOB)):
        candidate = child / SETTINGS_SUBPATH
        if candidate.is_dir():
            found.append(candidate)
    return found


def file_sha256(path: Path) -> str:
    """SHA-256 hash of a file, hex digest."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_or_none(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return file_sha256(path)
    except OSError:
        return None


def _write_version_stamp(target: Path, *, bundled_hash: str, used_symlink: bool) -> None:
    stamp = {
        "package_version": __version__,
        "script_sha256": bundled_hash,
        "installed_at": time.time(),
        "used_symlink": used_symlink,
    }
    stamp_path = target.with_suffix(target.suffix + VERSION_STAMP_SUFFIX)
    stamp_path.write_text(
        json.dumps(stamp, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def ensure_runtime_dirs(hardware_dir_path: Path) -> tuple[Path, ...]:
    """Pre-create the IPC runtime dirs that FL's sandboxed Python can't.

    FL Studio's embedded Python cannot ``os.makedirs`` fresh subdirs
    under ``~/Documents/...``, so the in-FL script's own
    ``_ensure_dirs()`` silently fails on first launch. We create them
    here from the (unsandboxed) MCP install process.
    """
    runtime_root = hardware_dir_path / RUNTIME_SUBDIR
    created: list[Path] = []
    for leaf in RUNTIME_LEAF_DIRS:
        leaf_dir = runtime_root / leaf
        leaf_dir.mkdir(parents=True, exist_ok=True)
        created.append(leaf_dir)
    return tuple(created)


def install_midi_script(
    *,
    hardware_dir_path: Path | None = None,
    prefer_symlink: bool = False,
    create_hardware_dir: bool = False,
) -> InstallResult:
    """Install or refresh the bundled MIDI script.

    Parameters
    ----------
    hardware_dir_path
        Override for FL's Hardware dir (else env var or default).
    prefer_symlink
        Symlink the bundled file instead of copying. Useful for dev
        loops; production installs default to copy so the file survives
        package upgrades cleanly.
    create_hardware_dir
        If True and the Hardware dir doesn't exist, create it. Default
        False — a missing dir usually means FL isn't installed and the
        installer should report that distinctly rather than silently
        creating it.
    """
    bundled = bundled_script_path()
    if not bundled.exists():
        raise FileNotFoundError(f"bundled MIDI script missing: {bundled}")

    hw_dir = hardware_dir(hardware_dir_path)
    if not hw_dir.exists():
        if not create_hardware_dir:
            _LOG.warning(
                "FL Hardware dir missing",
                extra={"hardware_dir": str(hw_dir)},
            )
            return InstallResult(
                action=InstallAction.HARDWARE_DIR_MISSING,
                target_path=hw_dir / SCRIPT_SUBDIR / SCRIPT_FILENAME,
                bundled_hash=file_sha256(bundled),
                installed_hash=None,
                used_symlink=False,
            )
        hw_dir.mkdir(parents=True, exist_ok=True)

    runtime_dirs = ensure_runtime_dirs(hw_dir)

    script_dir = hw_dir / SCRIPT_SUBDIR
    script_dir.mkdir(parents=True, exist_ok=True)
    target = script_dir / SCRIPT_FILENAME
    bundled_hash = file_sha256(bundled)
    installed_hash = _hash_or_none(target)

    if installed_hash == bundled_hash:
        _LOG.info(
            "MIDI script already up to date",
            extra={"target": str(target), "sha256": bundled_hash},
        )
        return InstallResult(
            action=InstallAction.NOOP,
            target_path=target,
            bundled_hash=bundled_hash,
            installed_hash=installed_hash,
            used_symlink=target.is_symlink(),
            runtime_dirs=runtime_dirs,
        )

    action = (
        InstallAction.UPDATED if target.exists() or target.is_symlink() else InstallAction.INSTALLED
    )

    if target.exists() or target.is_symlink():
        target.unlink()

    used_symlink = False
    if prefer_symlink:
        try:
            target.symlink_to(bundled)
            used_symlink = True
        except OSError as exc:
            _LOG.warning(
                "symlink failed; falling back to copy",
                extra={"target": str(target), "error": str(exc)},
            )
            shutil.copy2(bundled, target)
    else:
        shutil.copy2(bundled, target)

    _write_version_stamp(target, bundled_hash=bundled_hash, used_symlink=used_symlink)
    _LOG.info(
        "MIDI script installed",
        extra={
            "action": action.value,
            "target": str(target),
            "sha256": bundled_hash,
            "used_symlink": used_symlink,
        },
    )
    return InstallResult(
        action=action,
        target_path=target,
        bundled_hash=bundled_hash,
        installed_hash=installed_hash,
        used_symlink=used_symlink,
        runtime_dirs=runtime_dirs,
    )


def install_to_all_fl_versions(
    *,
    prefer_symlink: bool = False,
    parent: Path | None = None,
) -> list[InstallResult]:
    """Install the MIDI script into every detected FL Studio Hardware dir.

    Discovers ``~/Documents/Image-Line/FL Studio*/Settings/Hardware/``
    and installs into each. Idempotent — already-current installs
    return NOOP. If no dirs are found, returns a single
    HARDWARE_DIR_MISSING result for the default location so callers
    can surface a clear message.
    """
    dirs = discover_fl_hardware_dirs(parent)
    if not dirs:
        return [
            install_midi_script(
                hardware_dir_path=DEFAULT_FL_HARDWARE_DIR,
                prefer_symlink=prefer_symlink,
            )
        ]
    results: list[InstallResult] = []
    for hw in dirs:
        results.append(install_midi_script(hardware_dir_path=hw, prefer_symlink=prefer_symlink))
    return results


def installed_version_stamp(target: Path) -> dict | None:
    """Return parsed sidecar stamp for ``target`` or ``None`` if missing."""
    stamp_path = target.with_suffix(target.suffix + VERSION_STAMP_SUFFIX)
    if not stamp_path.exists():
        return None
    try:
        return json.loads(stamp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def iter_install_paths(
    hardware_dir_path: Path | None = None,
) -> Iterable[Path]:
    """Yield candidate install targets — useful for cleanup tooling."""
    yield hardware_dir(hardware_dir_path) / SCRIPT_FILENAME
