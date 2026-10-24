"""Phase 1: Structured JSON logging with secret redaction."""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any

_REDACT_PATTERN = re.compile(r"(api_key|secret|token|password|webhook_secret)", re.IGNORECASE)
_REDACTED = "***REDACTED***"


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *data* with sensitive values replaced."""
    result: dict[str, Any] = {}
    for k, v in data.items():
        if _REDACT_PATTERN.search(str(k)):
            result[k] = _REDACTED
        elif isinstance(v, dict):
            result[k] = redact_dict(v)
        else:
            result[k] = v
    return result


class SecretRedactionFilter(logging.Filter):
    """Redacts sensitive keys from any `extra` dict attached to a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        extra = record.__dict__.get("extra")
        if isinstance(extra, dict):
            record.__dict__["extra"] = redact_dict(extra)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = record.__dict__.get("extra")
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def build_json_formatter() -> logging.Formatter:
    return _JsonFormatter()


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(build_json_formatter())
        handler.addFilter(SecretRedactionFilter())
        root.addHandler(handler)
    else:
        for h in root.handlers:
            h.setFormatter(build_json_formatter())
            h.addFilter(SecretRedactionFilter())
