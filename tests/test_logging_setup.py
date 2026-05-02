"""Tests for the rotating JSON log setup."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from flstudio_mcp import logging_setup


@pytest.fixture
def tmp_log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


def _read_log(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_configure_logging_creates_dir_and_returns_path(tmp_log_dir: Path) -> None:
    path = logging_setup.configure_logging(log_dir=tmp_log_dir)
    assert path == tmp_log_dir / "server.log"
    assert tmp_log_dir.is_dir()


def test_log_record_is_single_line_json(tmp_log_dir: Path) -> None:
    log_path = logging_setup.configure_logging(log_dir=tmp_log_dir, level=logging.DEBUG)
    logger = logging_setup.get_logger("smoke")

    logger.info("hello", extra={"kind": "describe", "ok": True, "duration_ms": 12})
    for handler in logging.getLogger(logging_setup.LOGGER_NAME).handlers:
        handler.flush()

    entries = _read_log(log_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "INFO"
    assert entry["msg"] == "hello"
    assert entry["logger"].endswith("smoke")
    assert entry["kind"] == "describe"
    assert entry["ok"] is True
    assert entry["duration_ms"] == 12


def test_repeated_configure_replaces_handlers(tmp_log_dir: Path) -> None:
    logging_setup.configure_logging(log_dir=tmp_log_dir)
    first_count = len(logging.getLogger(logging_setup.LOGGER_NAME).handlers)
    logging_setup.configure_logging(log_dir=tmp_log_dir)
    second_count = len(logging.getLogger(logging_setup.LOGGER_NAME).handlers)
    assert first_count == second_count == 1


def test_read_recent_log_lines_returns_tail(tmp_log_dir: Path) -> None:
    log_path = logging_setup.configure_logging(log_dir=tmp_log_dir)
    logger = logging_setup.get_logger()
    for i in range(5):
        logger.info("event", extra={"n": i})
    for handler in logging.getLogger(logging_setup.LOGGER_NAME).handlers:
        handler.flush()

    tail = logging_setup.read_recent_log_lines(3, log_dir=tmp_log_dir)
    assert [e["n"] for e in tail] == [2, 3, 4]
    assert log_path.exists()


def test_read_recent_log_lines_missing_file(tmp_log_dir: Path) -> None:
    assert logging_setup.read_recent_log_lines(10, log_dir=tmp_log_dir) == []


def test_read_recent_log_lines_skips_corrupt(tmp_log_dir: Path) -> None:
    tmp_log_dir.mkdir()
    log_path = tmp_log_dir / "server.log"
    log_path.write_text(
        '{"ts":"t","level":"INFO","logger":"x","msg":"ok"}\n'
        "not-json\n"
        '{"ts":"t","level":"INFO","logger":"x","msg":"ok2"}\n',
        encoding="utf-8",
    )
    out = logging_setup.read_recent_log_lines(10, log_dir=tmp_log_dir)
    assert [e["msg"] for e in out] == ["ok", "ok2"]


def test_env_log_level_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLSTUDIO_MCP_LOG_LEVEL", raising=False)
    assert logging_setup.env_log_level() == logging.INFO


def test_env_log_level_uppercases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLSTUDIO_MCP_LOG_LEVEL", "debug")
    assert logging_setup.env_log_level() == "DEBUG"
