"""構造化ログ。ジョブID・原本ID・店舗・営業日・台番号・処理段階を contextvars で付与する。"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "slot_data_log_context", default=None
)  # type: ignore[arg-type]

CONTEXT_KEYS = (
    "job_id",
    "raw_id",
    "parse_run_id",
    "hall",
    "business_date",
    "machine_number",
    "stage",
    "source",
)
_SECRET_PATTERN = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|authorization|cookie)=([^\s&]+)", re.I
)
_URL_PASSWORD = re.compile(r"(\w+://[^:/\s]+:)([^@\s]+)(@)")


def redact(message: str) -> str:
    message = _SECRET_PATTERN.sub(r"\1=***", message)
    return _URL_PASSWORD.sub(r"\1***\3", message)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    merged = {**(_context.get() or {}), **{k: str(v) for k, v in values.items() if v is not None}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            **(_context.get() or {}),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(
                {
                    k: v if isinstance(v, int | float | bool) or v is None else redact(str(v))
                    for k, v in extra.items()
                }
            )
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = " ".join(f"{k}={v}" for k, v in (_context.get() or {}).items())
        base = f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} {redact(record.getMessage())}"
        return f"{base} [{ctx}]" if ctx else base


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_format else TextFormatter())
    root = logging.getLogger("slot_data")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("slot_data") else f"slot_data.{name}")
