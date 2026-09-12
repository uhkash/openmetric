"""End-to-end proxy behaviour against a mocked upstream."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlalchemy import select

from openmetric.db import session_scope
from openmetric.models import RequestEvent

CHAT_RESPONSE = {
    "id": "chatcmpl-test",
    "model": "openai/gpt-4o-mini",
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
}


class _Event(SimpleNamespace):
    """Detached snapshot of the most recent event, with relationships resolved."""


def _last_event() -> _Event:
    with session_scope() as db:
        event = db.scalar(select(RequestEvent).order_by(RequestEvent.id.desc()))
        assert event is not None, "no event was recorded"
        fields = {c.name: getattr(event, c.name) for c in RequestEvent.__table__.columns}
        return _Event(
            **fields,
            project=event.project,
            use_case=event.use_case,
            provider=event.provider,
            credential=event.credential,
        )


@respx.mock
def test_proxied_call_is_measured_and_priced(configured):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_RESPONSE)
    )
    response = configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert route.called
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hello"

    event = _last_event()
    assert event.input_tokens == 1000
    assert event.output_tokens == 500
    assert event.cost_source == "catalog"
    assert event.cost_usd == pytest.approx(0.15 * 0.001 + 0.60 * 0.0005)
    assert event.project.slug == "demo-app"
    assert event.use_case.slug == "llm-summary"
    assert event.credential.label == "openrouter-test"
    assert event.ok is True


@respx.mock
def test_provider_key_is_sent_upstream_and_virtual_key_is_not(configured):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_RESPONSE)
    )
    configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "openai/gpt-4o-mini", "messages": []},
    )

    sent = route.calls.last.request.headers
    assert sent["authorization"] == f"Bearer {configured['provider_secret']}"
    # The caller's virtual key must never reach the provider.
    assert configured["token"] not in str(dict(sent))


@respx.mock
def test_openmetric_headers_are_not_forwarded(configured):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_RESPONSE)
    )
    configured["client"].post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {configured['token']}",
            "X-OpenMetric-Project": "other-project",
            "X-OpenMetric-Use-Case": "scraping",
        },
        json={"model": "openai/gpt-4o-mini", "messages": []},
    )
    forwarded = {k.lower() for k in route.calls.last.request.headers}
    assert not any(name.startswith("x-openmetric-") for name in forwarded)


@respx.mock
def test_headers_override_the_virtual_keys_defaults(configured):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_RESPONSE)
    )
    configured["client"].post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {configured['token']}",
            "X-OpenMetric-Project": "side-quest",
            "X-OpenMetric-Use-Case": "classification",
        },
        json={"model": "openai/gpt-4o-mini", "messages": []},
    )
    event = _last_event()
    assert event.project.slug == "side-quest"  # auto-created on first use
    assert event.use_case.slug == "classification"


@respx.mock
def test_upstream_error_is_recorded_not_swallowed(configured):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    response = configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "openai/gpt-4o-mini", "messages": []},
    )
    assert response.status_code == 429
    event = _last_event()
    assert event.ok is False
    assert event.status_code == 429
    assert event.error_type == "http_429"


@respx.mock
def test_network_failure_returns_502_and_is_recorded(configured):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    response = configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "openai/gpt-4o-mini", "messages": []},
    )
    assert response.status_code == 502
    assert _last_event().status_code == 502


@respx.mock
def test_streaming_response_is_passed_through_and_measured(configured):
    stream = (
        b'data: {"model":"openai/gpt-4o-mini","choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"usage":{"prompt_tokens":2000,"completion_tokens":100}}\n\n'
        b"data: [DONE]\n\n"
    )
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=stream, headers={"content-type": "text/event-stream"}
        )
    )
    response = configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "openai/gpt-4o-mini", "messages": [], "stream": True},
    )
    assert response.status_code == 200
    assert b"[DONE]" in response.content

    event = _last_event()
    assert event.streamed is True
    assert event.input_tokens == 2000
    assert event.output_tokens == 100


@respx.mock
def test_upstream_reported_cost_wins_for_openrouter(configured):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "openai/gpt-4o-mini",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0425},
            },
        )
    )
    configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "openai/gpt-4o-mini", "messages": []},
    )
    event = _last_event()
    assert event.cost_source == "upstream"
    assert event.cost_usd == 0.0425


@respx.mock
def test_generic_proxy_route_measures_non_llm_providers(configured, client):
    """A scraping API has no tokens, so it is measured in units instead."""
    from openmetric import crypto
    from openmetric.models import Credential, Provider

    with session_scope() as db:
        provider = db.scalar(select(Provider).where(Provider.slug == "firecrawl"))
        secret = "fc-test-key-000000000000000000"
        db.add(
            Credential(
                provider_id=provider.id,
                label="firecrawl-test",
                encrypted_secret=crypto.encrypt(secret),
                key_hint=crypto.hint(secret),
                key_fingerprint=crypto.fingerprint(secret),
            )
        )

    route = respx.post("https://api.firecrawl.dev/v1/scrape").mock(
        return_value=httpx.Response(200, json={"data": [{"markdown": "# page"}]})
    )
    response = client.post(
        "/proxy/firecrawl/v1/scrape",
        headers={
            "Authorization": f"Bearer {configured['token']}",
            "X-OpenMetric-Project": "demo-app",
            "X-OpenMetric-Use-Case": "web-scraping",
        },
        json={"url": "https://example.com"},
    )
    assert route.called
    assert response.status_code == 200
    assert (
        route.calls.last.request.headers["authorization"] == "Bearer fc-test-key-000000000000000000"
    )

    event = _last_event()
    assert event.units == 1.0
    assert event.cost_source == "unknown"  # no rate configured yet - a blindspot, by design
    assert event.provider.slug == "firecrawl"


def test_call_without_a_usable_credential_is_rejected_clearly(client):
    response = client.post(
        "/proxy/together/v1/chat/completions",
        headers={"X-OpenMetric-Project": "demo-app"},
        json={"model": "x"},
    )
    assert response.status_code == 424
    assert "openmetric key add together" in response.json()["detail"]


def test_unknown_provider_is_rejected(client):
    response = client.post("/proxy/not-a-provider/v1/chat", json={})
    assert response.status_code == 404
    assert "Unknown provider" in response.json()["detail"]
