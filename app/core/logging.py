"""Structured logging: single JSON line per record.

Log level comes from the LOG_LEVEL environment variable (default INFO).
Every record is rendered as:
    {"ts": "2026-10-23T20:00:00.123456+00:00", "level": "INFO",
     "logger": "app.main", "msg": "...", ...extras}
Custom extras passed via logging `extra=` become top-level JSON fields
(e.g. correlation_id).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

LEVEL_ENV_VAR = "LOG_LEVEL"
DEFAULT_LEVEL = "INFO"

_RESERVED_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)

# Third-party loggers routed through the same JSON handler.
_ROUTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")

# Third-party extras that are display noise, not log data.
_NOISE_ATTRS = frozenset({"color_message"})


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key in _NOISE_ATTRS or key in entry:
                continue
            if key.startswith("_"):
                continue
            entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _resolve_level(level: str | None) -> int:
    raw = (level if level is not None else os.environ.get(LEVEL_ENV_VAR, DEFAULT_LEVEL)).upper()
    resolved = getattr(logging, raw, None)
    if not isinstance(resolved, int):
        # Deliberate stderr write: the JSON handler may not exist yet.
        print(f"WARNING: invalid {LEVEL_ENV_VAR}={raw!r}; using {DEFAULT_LEVEL}", file=sys.stderr)
        return logging.INFO
    return resolved


def setup_logging(level: str | None = None) -> None:
    """Configure root logging with the JSON handler (idempotent).

    `level` (e.g. "DEBUG") overrides the LOG_LEVEL environment variable,
    which defaults to INFO.
    """
    resolved = _resolve_level(level)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved)

    for name in _ROUTED_LOGGERS:
        log = logging.getLogger(name)
        log.handlers = []
        log.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
