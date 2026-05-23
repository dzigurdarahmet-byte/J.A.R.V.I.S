"""Structured logging via structlog with redaction of secrets.

Setup once at app startup via `setup_logging()`. Then use `get_logger(__name__)`.

All logs go to stdout as JSON in production, pretty-printed in development.
Secrets matching common API key patterns are auto-redacted.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from core.config import settings

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),  # Anthropic
    re.compile(r"sk-[A-Za-z0-9]{48}"),  # OpenAI
    re.compile(r"gsk_[A-Za-z0-9]{40,}"),  # Groq
    re.compile(r"\d{8,12}:[A-Za-z0-9_-]{30,}"),  # Telegram bot token
]


def _redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from any string field in the event."""

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            for pattern in SECRET_PATTERNS:
                value = pattern.sub("[REDACTED]", value)
            return value
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return {k: scrub(v) for k, v in event_dict.items()}


def setup_logging() -> None:
    """Configure structlog. Call once at app startup."""
    log_level = getattr(logging, settings.jarvis_log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact,
    ]

    if settings.is_production:
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Get a structured logger. Pass `__name__` for module identification."""
    return structlog.get_logger(name)
