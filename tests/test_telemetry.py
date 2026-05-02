"""Tests for the telemetry adapter."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from flstudio_mcp import logging_setup, telemetry


@pytest.fixture
def configured_logs(tmp_path: Path) -> Path:
    log_dir = tmp_path / "logs"
    return logging_setup.configure_logging(log_dir=log_dir)


def _read(path: Path) -> list[dict]:
    for handler in logging.getLogger(logging_setup.LOGGER_NAME).handlers:
        handler.flush()
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_record_event_writes_to_logger(configured_logs: Path) -> None:
    telemetry.record_event("describe", True, 12.3, source="test")
    entries = _read(configured_logs)
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "describe"
    assert e["ok"] is True
    assert e["duration_ms"] == 12.3
    assert e["source"] == "test"
    assert e["msg"] == "event"


def test_telemetry_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(telemetry.TELEMETRY_ENV_VAR, raising=False)
    assert telemetry.telemetry_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_telemetry_enabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(telemetry.TELEMETRY_ENV_VAR, value)
    assert telemetry.telemetry_enabled() is True


def test_set_backend_invoked_only_when_enabled(
    configured_logs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(telemetry.TELEMETRY_ENV_VAR, raising=False)
    calls: list[tuple] = []

    def fake_backend(kind, ok, duration_ms, meta):
        calls.append((kind, ok, duration_ms, meta))

    telemetry.set_backend(fake_backend)
    try:
        telemetry.record_event("noop", True, 1.0)
        assert calls == []  # disabled — backend not called

        monkeypatch.setenv(telemetry.TELEMETRY_ENV_VAR, "1")
        telemetry.record_event("noop", True, 1.0, foo="bar")
        assert calls == [("noop", True, 1.0, {"foo": "bar"})]
    finally:
        telemetry.set_backend(None)


def test_set_backend_none_reverts_to_logger(configured_logs: Path) -> None:
    telemetry.set_backend(None)  # idempotent
    telemetry.record_event("k", False, 0.5)
    entries = _read(configured_logs)
    assert entries[-1]["kind"] == "k"
