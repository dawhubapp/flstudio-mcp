"""Structured rotating JSON logs for the MCP server.

Logs go to ``~/Library/Logs/flstudio-mcp/server.log`` by default (macOS
convention). One JSON object per line. The file rotates at 1 MiB with up
to 5 backups kept. Console output is suppressed by default — stderr is
reserved for the MCP transport handshake.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "flstudio-mcp"
DEFAULT_LOG_FILENAME = "server.log"
DEFAULT_MAX_BYTES = 1_048_576  # 1 MiB
DEFAULT_BACKUP_COUNT = 5
LOGGER_NAME = "flstudio_mcp"

_RESERVED_LOGRECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render each LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = _coerce_jsonable(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _iso_utc(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def _coerce_jsonable(value: object) -> object:
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def configure_logging(
    *,
    log_dir: Path | None = None,
    filename: str = DEFAULT_LOG_FILENAME,
    level: int | str = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    capture_stderr: bool = False,
) -> Path:
    """Wire up the package logger; return the resolved log file path.

    Idempotent: a second call with the same target replaces existing
    handlers on the package logger.
    """
    target_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / filename

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False

    if capture_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(JsonFormatter())
        logger.addHandler(stderr_handler)

    return log_path


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the package logger or a child logger of it."""
    if name is None or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(LOGGER_NAME).getChild(name)


def read_recent_log_lines(
    n: int,
    *,
    log_dir: Path | None = None,
    filename: str = DEFAULT_LOG_FILENAME,
) -> list[Mapping[str, object]]:
    """Return the most recent ``n`` JSON log records, oldest first.

    Skips lines that fail to parse (e.g. partial writes during rotation).
    Returns ``[]`` if the log file does not exist.
    """
    if n <= 0:
        return []
    target_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    log_path = target_dir / filename
    if not log_path.exists():
        return []
    raw = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = raw[-n:]
    parsed: list[Mapping[str, object]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            parsed.append(obj)
    return parsed


def env_log_level(default: int = logging.INFO) -> int | str:
    """Return the log level from ``FLSTUDIO_MCP_LOG_LEVEL`` if set."""
    value = os.environ.get("FLSTUDIO_MCP_LOG_LEVEL")
    if not value:
        return default
    return value.upper()
