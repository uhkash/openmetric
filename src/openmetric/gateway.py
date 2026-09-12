"""The proxy. Your app talks to OpenMetric; OpenMetric talks to the provider.

What the gateway adds on the way through:

* your application never holds a provider key - it holds an OpenMetric virtual key
* every call is tagged with project + use case before it leaves the machine
* the response is measured (tokens, units, latency, status) and priced
* nothing about the call's *content* is stored unless you explicitly opt in
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import pricing
from . import usage as usage_mod
from .config import get_settings
from .crypto import decrypt, hash_virtual_key
from .db import session_scope
from .models import Credential, Project, Provider, RequestEvent, UseCase, VirtualKey
from .redaction import redact_text, safe_error

log = logging.getLogger("openmetric.gateway")
router = APIRouter()

# Headers we must not pass upstream: connection-level plumbing, plus the client's
# own credentials (which are OpenMetric virtual keys, not provider keys).
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "authorization",
    "x-api-key",
    "api-key",
}
OPENMETRIC_HEADER_PREFIX = "x-openmetric-"

MAX_CAPTURED_STREAM_BYTES = 2 * 1024 * 1024
MAX_LOGGED_BODY_CHARS = 20_000


@dataclass
class CallContext:
    """Everything we know about a call before it is sent upstream."""

    provider: Provider
    credential: Credential
    project: Project | None = None
    use_case: UseCase | None = None
    virtual_key: VirtualKey | None = None
    tags: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _client_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("x-openmetric-key") or "").strip()


def resolve_virtual_key(session: Session, token: str) -> VirtualKey | None:
    if not token:
        return None
    return session.scalar(
        select(VirtualKey).where(
            VirtualKey.token_hash == hash_virtual_key(token), VirtualKey.active.is_(True)
        )
    )


def _get_or_create_project(session: Session, slug: str) -> Project:
    slug = slug.strip().lower()
    project = session.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        project = Project(slug=slug, name=slug.replace("-", " ").title())
        session.add(project)
        session.flush()
        log.info("auto-created project %s", slug)
    return project


def _get_or_create_use_case(session: Session, slug: str) -> UseCase:
    slug = slug.strip().lower()
    use_case = session.scalar(select(UseCase).where(UseCase.slug == slug))
    if use_case is None:
        use_case = UseCase(slug=slug, name=slug.replace("-", " ").title())
        session.add(use_case)
        session.flush()
        log.info("auto-created use case %s", slug)
    return use_case


def select_credential(
    session: Session,
    provider: Provider,
    project: Project | None,
    label: str | None = None,
) -> Credential | None:
    """Pick the key to bill this call to.

    Explicit label wins, then the key pinned to this project, then a shared key
    for the provider. "Which key paid for this?" always has an answer.
    """
    base = select(Credential).where(
        Credential.provider_id == provider.id, Credential.active.is_(True)
    )
    if label:
        return session.scalar(base.where(Credential.label == label))
    if project is not None:
        scoped = session.scalar(base.where(Credential.project_id == project.id))
        if scoped is not None:
            return scoped
    return session.scalar(base.where(Credential.project_id.is_(None)))


def _parse_tags(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"tag": parsed}
    except json.JSONDecodeError:
        return {"tag": raw[:200]}


def build_context(
    session: Session,
    request: Request,
    provider_slug: str | None,
    model: str | None = None,
) -> CallContext:
    """Turn headers + virtual key into a fully resolved call context."""
    virtual_key = resolve_virtual_key(session, _client_token(request))

    project: Project | None = None
    use_case: UseCase | None = None
    if virtual_key is not None:
        project = virtual_key.project
        use_case = virtual_key.use_case
        virtual_key.last_used_at = datetime.now(timezone.utc)

    # Per-request headers override the virtual key's defaults, so one key can
    # still serve several use cases inside the same project.
    if header_project := request.headers.get("x-openmetric-project"):
        project = _get_or_create_project(session, header_project)
    if header_use_case := request.headers.get("x-openmetric-use-case"):
        use_case = _get_or_create_use_case(session, header_use_case)

    # Model may carry an explicit provider: "openrouter::anthropic/claude-sonnet-4"
    if model and "::" in model:
        provider_slug = model.split("::", 1)[0]
    slug = (provider_slug or request.headers.get("x-openmetric-provider") or "").strip().lower()

    provider: Provider | None = None
    if slug:
        provider = session.scalar(select(Provider).where(Provider.slug == slug))
        if provider is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Unknown provider '{slug}'. Add it with "
                    f"`openmetric provider add {slug} --base-url ...`, or list the built-ins "
                    f"with `openmetric providers`."
                ),
            )
    elif virtual_key is not None and virtual_key.credential is not None:
        provider = virtual_key.credential.provider

    if provider is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No provider resolved for this call. Send an X-OpenMetric-Provider header, "
                "use the /proxy/{provider}/... route, or bind the virtual key to a credential."
            ),
        )

    credential = None
    if virtual_key is not None and virtual_key.credential is not None:
        if virtual_key.credential.provider_id == provider.id and virtual_key.credential.active:
            credential = virtual_key.credential
    if credential is None:
        credential = select_credential(
            session, provider, project, request.headers.get("x-openmetric-credential")
        )
    if credential is None:
        raise HTTPException(
            status_code=424,
            detail=(
                f"No active credential for provider '{provider.slug}'"
                + (f" and project '{project.slug}'" if project else "")
                + f". Add one with `openmetric key add {provider.slug} --label <name>`."
            ),
        )

    return CallContext(
        provider=provider,
        credential=credential,
        project=project,
        use_case=use_case,
        virtual_key=virtual_key,
        tags=_parse_tags(request.headers.get("x-openmetric-tags")),
    )


# --------------------------------------------------------------------------- #
# Upstream request construction
# --------------------------------------------------------------------------- #


def forwarded_headers(request: Request, ctx: CallContext, secret: str) -> dict[str, str]:
    """Client headers, minus plumbing and client credentials, plus provider auth."""
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and not k.lower().startswith(OPENMETRIC_HEADER_PREFIX)
    }
    style = ctx.provider.auth_style
    if style == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif style == "header":
        headers[ctx.provider.auth_header or "x-api-key"] = secret
    elif style == "basic":
        headers["Authorization"] = f"Basic {secret}"
    # "query" style is applied to the URL instead.

    if ctx.provider.slug == "anthropic":
        headers.setdefault("anthropic-version", "2023-06-01")
    if ctx.provider.slug == "openrouter":
        # Identify the gateway. Harmless, and OpenRouter uses it for attribution.
        headers.setdefault("HTTP-Referer", "https://github.com/uhkash/openmetric")
        headers.setdefault("X-Title", "OpenMetric")
    return headers


def upstream_url(ctx: CallContext, path: str, query: str, secret: str) -> str:
    base = ctx.provider.base_url.rstrip("/")
    url = f"{base}/{path.lstrip('/')}" if path else base
    params = [p for p in [query] if p]
    if ctx.provider.auth_style == "query" and ctx.provider.auth_query_param:
        params.append(f"{ctx.provider.auth_query_param}={secret}")
    return f"{url}?{'&'.join(params)}" if params else url


def _strip_provider_marker(body: bytes) -> bytes:
    """Remove the ``provider::`` prefix from ``model`` before forwarding."""
    if b"::" not in body[:4096]:
        return body
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if isinstance(payload, dict) and isinstance(payload.get("model"), str):
        if "::" in payload["model"]:
            payload["model"] = payload["model"].split("::", 1)[1]
            return json.dumps(payload).encode()
    return body


def _model_from_body(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    return str(payload.get("model", "")) if isinstance(payload, dict) else ""


def _wants_stream(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return bool(isinstance(payload, dict) and payload.get("stream"))


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


def _truncate(text: str) -> str:
    return text[:MAX_LOGGED_BODY_CHARS]


def record_event(
    *,
    ctx_ids: dict[str, int | None],
    model: str,
    operation: str,
    method: str,
    path: str,
    status_code: int,
    latency_ms: int,
    streamed: bool,
    usage: usage_mod.Usage,
    provider_kind: str,
    provider_slug: str,
    prefers_upstream_cost: bool,
    request_bytes: int,
    response_bytes: int,
    tags: dict[str, Any] | None,
    error_type: str = "",
    request_body: str | None = None,
    response_body: str | None = None,
) -> None:
    """Persist one call. Never raises into the request path."""
    settings = get_settings()
    cost, source = 0.0, pricing.UNKNOWN

    if usage.upstream_cost_usd is not None and (prefers_upstream_cost or not usage.has_tokens):
        cost, source = float(usage.upstream_cost_usd), pricing.UPSTREAM
    elif usage.has_tokens:
        cost, source = pricing.price_tokens(
            model or usage.model, usage.input_tokens, usage.output_tokens, usage.cached_tokens
        )
        if source == pricing.UNKNOWN and usage.upstream_cost_usd is not None:
            cost, source = float(usage.upstream_cost_usd), pricing.UPSTREAM

    unit_kind = ""
    units = usage.units
    if not usage.has_tokens and provider_kind != "llm":
        cost, source, unit_kind = pricing.price_units(provider_slug, units or 1.0)
        units = units or 1.0

    try:
        with session_scope() as session:
            event = RequestEvent(
                project_id=ctx_ids.get("project_id"),
                use_case_id=ctx_ids.get("use_case_id"),
                provider_id=ctx_ids.get("provider_id"),
                credential_id=ctx_ids.get("credential_id"),
                virtual_key_id=ctx_ids.get("virtual_key_id"),
                model=(model or usage.model or "")[:200],
                operation=operation[:64],
                method=method,
                path=path[:500],
                status_code=status_code,
                ok=200 <= status_code < 400,
                error_type=error_type[:64],
                latency_ms=latency_ms,
                streamed=streamed,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                units=units,
                unit_kind=unit_kind,
                cost_usd=cost,
                cost_source=source,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                tags=tags,
                request_body=(
                    _truncate(redact_text(request_body or ""))
                    if settings.log_request_bodies
                    else None
                ),
                response_body=(
                    _truncate(redact_text(response_body or ""))
                    if settings.log_response_bodies
                    else None
                ),
            )
            session.add(event)
            if credential_id := ctx_ids.get("credential_id"):
                credential = session.get(Credential, credential_id)
                if credential is not None:
                    credential.last_used_at = datetime.now(timezone.utc)
    except Exception as exc:  # metering must never break the caller's request
        log.error("failed to record event: %s", safe_error(exc))


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


async def _proxy(request: Request, provider_slug: str | None, path: str) -> Any:
    settings = get_settings()
    body = await request.body()
    model = _model_from_body(body)

    with session_scope() as session:
        ctx = build_context(session, request, provider_slug, model)
        secret = decrypt(ctx.credential.encrypted_secret)
        ctx_ids = {
            "project_id": ctx.project.id if ctx.project else None,
            "use_case_id": ctx.use_case.id if ctx.use_case else None,
            "provider_id": ctx.provider.id,
            "credential_id": ctx.credential.id,
            "virtual_key_id": ctx.virtual_key.id if ctx.virtual_key else None,
        }
        provider_kind = ctx.provider.kind
        provider_slug_resolved = ctx.provider.slug
        tags = ctx.tags
        headers = forwarded_headers(request, ctx, secret)
        url = upstream_url(ctx, path, request.url.query, secret)

    prefers_upstream = bool(
        pricing.provider_defaults(provider_slug_resolved).get("prefers_upstream_cost")
    )
    outbound = _strip_provider_marker(body)
    operation = usage_mod.operation_from_path(path)
    streaming = _wants_stream(outbound) or "text/event-stream" in request.headers.get("accept", "")
    started = time.perf_counter()

    client = httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=False)
    try:
        upstream_request = client.build_request(
            request.method, url, headers=headers, content=outbound or None
        )
        response = await client.send(upstream_request, stream=streaming)
    except httpx.HTTPError as exc:
        await client.aclose()
        latency = int((time.perf_counter() - started) * 1000)
        record_event(
            ctx_ids=ctx_ids,
            model=model,
            operation=operation,
            method=request.method,
            path=path,
            status_code=502,
            latency_ms=latency,
            streamed=streaming,
            usage=usage_mod.Usage(),
            provider_kind=provider_kind,
            provider_slug=provider_slug_resolved,
            prefers_upstream_cost=prefers_upstream,
            request_bytes=len(body),
            response_bytes=0,
            tags=tags,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"Upstream request failed: {safe_error(exc)}",
                    "type": "openmetric_upstream_error",
                    "provider": provider_slug_resolved,
                }
            },
        )

    passthrough_headers = {k: v for k, v in response.headers.items() if k.lower() not in HOP_BY_HOP}

    if not streaming:
        raw = await response.aread()
        await client.aclose()
        latency = int((time.perf_counter() - started) * 1000)
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            decoded = None
        parsed = usage_mod.extract_usage(decoded)
        if not parsed.has_tokens:
            parsed.units = usage_mod.guess_units(provider_kind, decoded)
        record_event(
            ctx_ids=ctx_ids,
            model=model or parsed.model,
            operation=operation,
            method=request.method,
            path=path,
            status_code=response.status_code,
            latency_ms=latency,
            streamed=False,
            usage=parsed,
            provider_kind=provider_kind,
            provider_slug=provider_slug_resolved,
            prefers_upstream_cost=prefers_upstream,
            request_bytes=len(body),
            response_bytes=len(raw),
            tags=tags,
            error_type="" if response.is_success else f"http_{response.status_code}",
            request_body=body.decode("utf-8", "replace") if body else None,
            response_body=raw.decode("utf-8", "replace") if raw else None,
        )
        return JSONResponse(
            status_code=response.status_code,
            content=decoded if decoded is not None else {"raw": raw.decode("utf-8", "replace")},
            headers={"content-type": "application/json"},
        )

    async def stream_and_measure():
        captured: list[bytes] = []
        captured_bytes = 0
        total_bytes = 0
        try:
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if captured_bytes < MAX_CAPTURED_STREAM_BYTES:
                    captured.append(chunk)
                    captured_bytes += len(chunk)
                yield chunk
        finally:
            latency = int((time.perf_counter() - started) * 1000)
            parsed = usage_mod.extract_usage_from_sse(captured)
            await response.aclose()
            await client.aclose()
            record_event(
                ctx_ids=ctx_ids,
                model=model or parsed.model,
                operation=operation,
                method=request.method,
                path=path,
                status_code=response.status_code,
                latency_ms=latency,
                streamed=True,
                usage=parsed,
                provider_kind=provider_kind,
                provider_slug=provider_slug_resolved,
                prefers_upstream_cost=prefers_upstream,
                request_bytes=len(body),
                response_bytes=total_bytes,
                tags=tags,
                error_type="" if response.is_success else f"http_{response.status_code}",
                request_body=body.decode("utf-8", "replace") if body else None,
            )

    return StreamingResponse(
        stream_and_measure(),
        status_code=response.status_code,
        headers=passthrough_headers,
        media_type=response.headers.get("content-type", "text/event-stream"),
    )


@router.api_route(
    "/proxy/{provider_slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
async def proxy_any(request: Request, provider_slug: str, path: str):
    """Generic passthrough: any provider, any endpoint, still measured."""
    return await _proxy(request, provider_slug, path)


@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def openai_compatible(request: Request, path: str):
    """Drop-in OpenAI-compatible endpoint.

    Point any OpenAI SDK at ``http://localhost:8099/v1`` with an OpenMetric
    virtual key and every call is measured with no other code change.
    """
    return await _proxy(request, None, path)
