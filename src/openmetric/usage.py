"""Pulling token counts, model names and upstream costs out of provider responses.

Every provider spells this differently. Normalising it here is most of the
reason a single gateway is worth having: one shape of number, whatever you called.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    upstream_cost_usd: float | None = None
    units: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tokens(self) -> bool:
        return bool(self.input_tokens or self.output_tokens)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def extract_usage(body: Any) -> Usage:
    """Read a decoded JSON response body from any supported provider."""
    usage = Usage()
    if not isinstance(body, dict):
        return usage

    usage.model = str(body.get("model") or "")
    raw = body.get("usage") or body.get("usageMetadata") or {}
    if not isinstance(raw, dict):
        return usage

    # OpenAI / OpenRouter / Groq / Together / DeepSeek / Mistral
    usage.input_tokens = _as_int(raw.get("prompt_tokens"))
    usage.output_tokens = _as_int(raw.get("completion_tokens"))

    # Anthropic
    if not usage.input_tokens:
        usage.input_tokens = _as_int(raw.get("input_tokens"))
    if not usage.output_tokens:
        usage.output_tokens = _as_int(raw.get("output_tokens"))

    # Google Gemini
    if not usage.input_tokens:
        usage.input_tokens = _as_int(raw.get("promptTokenCount"))
    if not usage.output_tokens:
        usage.output_tokens = _as_int(raw.get("candidatesTokenCount"))

    details = raw.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        usage.cached_tokens = _as_int(details.get("cached_tokens"))
    if not usage.cached_tokens:
        usage.cached_tokens = _as_int(raw.get("cache_read_input_tokens"))
    if not usage.cached_tokens:
        usage.cached_tokens = _as_int(raw.get("cachedContentTokenCount"))

    # Anthropic bills cache writes/reads on top of input_tokens.
    usage.input_tokens += _as_int(raw.get("cache_creation_input_tokens"))
    usage.input_tokens += _as_int(raw.get("cache_read_input_tokens"))

    # OpenRouter returns a real dollar amount when asked for it.
    for key in ("cost", "total_cost"):
        if raw.get(key) is not None:
            try:
                usage.upstream_cost_usd = float(raw[key])
                break
            except (TypeError, ValueError):
                pass

    return usage


def extract_usage_from_sse(chunks: list[bytes]) -> Usage:
    """Scan a captured SSE stream for the usage payload, which arrives last."""
    merged = Usage()
    for raw_line in b"".join(chunks).splitlines():
        line = raw_line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(event, dict):
            continue

        if event.get("model") and not merged.model:
            merged.model = str(event["model"])

        # Anthropic streams usage across message_start / message_delta events.
        candidate_sources = [event]
        if isinstance(event.get("message"), dict):
            candidate_sources.append(event["message"])
        for source in candidate_sources:
            parsed = extract_usage(source)
            if parsed.has_tokens:
                merged.input_tokens = max(merged.input_tokens, parsed.input_tokens)
                merged.output_tokens = max(merged.output_tokens, parsed.output_tokens)
                merged.cached_tokens = max(merged.cached_tokens, parsed.cached_tokens)
            if parsed.upstream_cost_usd is not None:
                merged.upstream_cost_usd = parsed.upstream_cost_usd
            if parsed.model and not merged.model:
                merged.model = parsed.model
    return merged


def guess_units(provider_kind: str, body: Any) -> float:
    """Billing units for non-LLM calls: one per request, or per returned item."""
    if provider_kind not in {"scrape", "search", "other"}:
        return 0.0
    if isinstance(body, dict):
        for key in ("results", "data", "organic", "items"):
            value = body.get(key)
            if isinstance(value, list) and value:
                return float(len(value))
    return 1.0


def operation_from_path(path: str) -> str:
    """A short label for what was called, used for grouping in the dashboard."""
    tail = (path or "").strip("/").split("?")[0]
    if not tail:
        return "request"
    known = {
        "chat/completions": "chat",
        "v1/chat/completions": "chat",
        "completions": "completion",
        "embeddings": "embedding",
        "messages": "chat",
        "v1/messages": "chat",
        "images/generations": "image",
        "audio/transcriptions": "transcription",
        "rerank": "rerank",
    }
    for suffix, label in known.items():
        if tail.endswith(suffix):
            return label
    return tail.split("/")[-1][:64] or "request"
