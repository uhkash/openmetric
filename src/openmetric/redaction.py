"""Scrubbing of secret-looking strings from anything that leaves the process.

Applied to log lines, error messages and upstream error bodies. It is deliberately
eager: a false positive costs you a few stars in a log line, a false negative
costs you a leaked key in a GitHub issue.
"""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "x-auth-token",
    "cookie",
    "set-cookie",
    "x-openmetric-key",
}

SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "authorization",
    "private_key",
    "encrypted_secret",
}

# Common vendor key shapes. Ordered longest-prefix-first so the widest match wins.
_KEY_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{16,}"),  # OpenRouter
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),  # Anthropic
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{16,}"),  # OpenAI project keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI classic
    re.compile(r"fc-[A-Za-z0-9]{20,}"),  # Firecrawl
    re.compile(r"tvly-[A-Za-z0-9_\-]{16,}"),  # Tavily
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),  # Groq
    re.compile(r"xai-[A-Za-z0-9]{20,}"),  # xAI
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),  # Google
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),  # GitHub
    re.compile(r"om_(?:live|test)_[A-Za-z0-9_\-]{16,}"),  # OpenMetric virtual keys
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
]

REDACTED = "[REDACTED]"
_MAX_SCAN = 200_000


def redact_text(value: str) -> str:
    """Replace anything that looks like a credential with ``[REDACTED]``."""
    if not value:
        return value
    out = value[:_MAX_SCAN]
    for pattern in _KEY_PATTERNS:
        out = pattern.sub(
            lambda m: "Bearer " + REDACTED if m.group(0).lower().startswith("bearer") else REDACTED,
            out,
        )
    return out + value[_MAX_SCAN:] if len(value) > _MAX_SCAN else out


def redact_headers(headers: Any) -> dict[str, str]:
    """Header mapping with sensitive values masked, safe to log or display."""
    items = headers.items() if hasattr(headers, "items") else dict(headers).items()
    return {
        str(k): (REDACTED if str(k).lower() in SENSITIVE_HEADERS else redact_text(str(v)))
        for k, v in items
    }


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secret-looking values inside dicts and lists."""
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        return {
            k: (
                REDACTED
                if str(k).lower() in SENSITIVE_FIELD_NAMES or str(k).lower() in SENSITIVE_HEADERS
                else redact_obj(v, _depth + 1)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def safe_error(exc: BaseException) -> str:
    """Exception text with any embedded credential stripped."""
    return redact_text(f"{type(exc).__name__}: {exc}")
