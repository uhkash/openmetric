"""Turning a response into a dollar amount.

Precedence, highest first:

1. ``upstream``  - the provider told us what the call cost (OpenRouter does this).
2. ``catalog``   - we multiplied token counts by a rate from the catalog.
3. ``unknown``   - we could not price it. The call is counted, the cost is not,
                   and it shows up under Blindspots as *unpriced spend*. Silent
                   zeros are the thing this project exists to prevent.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

BUILTIN_CATALOG = Path(__file__).parent / "data" / "catalog.yaml"

UPSTREAM = "upstream"
CATALOG = "catalog"
UNKNOWN = "unknown"


def local_pricing_path() -> Path:
    return Path(os.environ.get("OPENMETRIC_PRICING_FILE", "pricing.local.yaml"))


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


_catalog: dict[str, Any] | None = None
_catalog_stamp: tuple[str, float, int] | None = None


def _local_stamp() -> tuple[str, float, int]:
    """Identity of the override file: path, mtime, size. Size catches same-second edits."""
    path = local_pricing_path()
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime, stat.st_size)
    except OSError:
        return (str(path), 0.0, -1)


def load_catalog() -> dict[str, Any]:
    """Built-in catalog merged with ./pricing.local.yaml if present.

    Cached, but re-read whenever the override file changes on disk. Without that,
    `openmetric price set` would only take effect after restarting the gateway -
    and the CLI would be lying when it says new calls are priced with it.
    """
    global _catalog, _catalog_stamp
    stamp = _local_stamp()
    if _catalog is not None and stamp == _catalog_stamp:
        return _catalog

    catalog = yaml.safe_load(BUILTIN_CATALOG.read_text()) or {}
    local = local_pricing_path()
    if local.exists():
        overrides = yaml.safe_load(local.read_text()) or {}
        catalog = _deep_merge(catalog, overrides)
    _catalog, _catalog_stamp = catalog, stamp
    return catalog


def reload_catalog() -> None:
    """Force a re-read on the next lookup (used by the CLI and tests)."""
    global _catalog, _catalog_stamp
    _catalog = None
    _catalog_stamp = None


def provider_defaults(slug: str) -> dict[str, Any]:
    return dict(load_catalog().get("providers", {}).get(slug, {}))


def known_providers() -> dict[str, dict[str, Any]]:
    return dict(load_catalog().get("providers", {}))


def normalize_model(model: str) -> str:
    return (model or "").strip().lower()


def find_token_rate(model: str) -> dict[str, float] | None:
    """Exact match, then vendor-prefixed suffix, then longest ``prefix*`` pattern."""
    prices: dict[str, dict] = load_catalog().get("token_prices", {}) or {}
    if not model:
        return None
    name = normalize_model(model)
    lookup = {normalize_model(k): v for k, v in prices.items()}

    if name in lookup:
        return lookup[name]

    # "anthropic/claude-sonnet-4" -> "claude-sonnet-4", and drop ":free"/":nitro"
    tail = name.split("/")[-1].split(":")[0]
    if tail in lookup:
        return lookup[tail]

    best: tuple[int, dict] | None = None
    for pattern, rate in lookup.items():
        if pattern.endswith("*"):
            stem = pattern[:-1]
            if (name.startswith(stem) or tail.startswith(stem)) and (
                best is None or len(stem) > best[0]
            ):
                best = (len(stem), rate)
    return best[1] if best else None


def price_tokens(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> tuple[float, str]:
    """Cost of an LLM call from token counts. Returns ``(usd, source)``."""
    rate = find_token_rate(model)
    if rate is None:
        return 0.0, UNKNOWN
    billable_input = max(input_tokens - cached_tokens, 0)
    cached_rate = rate.get("cached_input", rate.get("input", 0.0))
    cost = (
        billable_input * float(rate.get("input", 0.0))
        + cached_tokens * float(cached_rate)
        + output_tokens * float(rate.get("output", 0.0))
    ) / 1_000_000
    return round(cost, 8), CATALOG


def price_units(provider_slug: str, units: float = 1.0) -> tuple[float, str, str]:
    """Cost of a non-LLM call (a scrape, a search). Returns ``(usd, source, unit_kind)``."""
    defaults = provider_defaults(provider_slug)
    unit_price = defaults.get("unit_price_usd")
    unit_kind = defaults.get("unit_kind", "request")
    if unit_price is None:
        return 0.0, UNKNOWN, unit_kind
    return round(float(unit_price) * units, 8), CATALOG, unit_kind


def set_local_price(
    provider: str | None = None,
    per_request: float | None = None,
    model: str | None = None,
    input_rate: float | None = None,
    output_rate: float | None = None,
) -> Path:
    """Write a price override into ./pricing.local.yaml (contains no secrets)."""
    path = local_pricing_path()
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}

    if provider and per_request is not None:
        data.setdefault("providers", {}).setdefault(provider, {})["unit_price_usd"] = per_request
    if model:
        entry = data.setdefault("token_prices", {}).setdefault(model, {})
        if input_rate is not None:
            entry["input"] = input_rate
        if output_rate is not None:
            entry["output"] = output_rate

    path.write_text(yaml.safe_dump(data, sort_keys=True))
    reload_catalog()
    return path
