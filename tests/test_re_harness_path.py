"""Tests for the re_harness path resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flstudio_mcp import _re_harness_path


def _make_fake_re_harness(root: Path) -> Path:
    pkg_root = root / "tools"
    (pkg_root / "re_harness").mkdir(parents=True)
    (pkg_root / "re_harness" / "ipc.py").write_text("")
    return pkg_root


def test_env_var_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg_root = _make_fake_re_harness(tmp_path)
    monkeypatch.setenv("FLSTUDIO_MCP_RE_HARNESS", str(pkg_root))

    if str(pkg_root) in sys.path:
        sys.path.remove(str(pkg_root))

    resolved = _re_harness_path.ensure_re_harness_on_path()
    assert resolved == pkg_root.resolve()
    assert str(pkg_root.resolve()) in sys.path


def test_dev_layout_resolves_to_real_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLSTUDIO_MCP_RE_HARNESS", raising=False)
    resolved = _re_harness_path.ensure_re_harness_on_path()
    assert (resolved / "re_harness" / "ipc.py").exists()


def test_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLSTUDIO_MCP_RE_HARNESS", str(tmp_path / "nope"))
    monkeypatch.setattr(_re_harness_path, "_candidate_paths", lambda: [tmp_path / "nope"])
    with pytest.raises(_re_harness_path.ReHarnessNotAvailable):
        _re_harness_path.ensure_re_harness_on_path()
