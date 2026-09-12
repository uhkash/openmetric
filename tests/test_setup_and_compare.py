"""The onboarding endpoint and previous-period comparison that drive the dashboard."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openmetric import analytics
from openmetric.db import session_scope
from openmetric.models import Provider, RequestEvent


def test_setup_on_a_fresh_install_lists_every_step_undone(client):
    status = client.get("/api/setup").json()
    assert status["complete"] is False
    by_id = {s["id"]: s for s in status["steps"]}
    assert by_id["encryption"]["done"] is True  # the test fixture sets a key
    assert by_id["credential"]["done"] is False
    assert by_id["virtual_key"]["done"] is False
    assert by_id["first_request"]["done"] is False
    assert status["counts"]["events"] == 0
    assert status["gateway"]["port"]


def test_setup_progresses_as_things_are_configured(configured):
    client = configured["client"]
    status = client.get("/api/setup").json()
    by_id = {s["id"]: s for s in status["steps"]}
    assert by_id["credential"]["done"] is True
    assert by_id["virtual_key"]["done"] is True
    assert by_id["first_request"]["done"] is False


def test_setup_names_unpriced_providers(client):
    with session_scope() as db:
        provider = db.scalar(select(Provider).where(Provider.slug == "firecrawl"))
        db.add(
            RequestEvent(provider_id=provider.id, model="", cost_source="unknown", status_code=200)
        )
    status = client.get("/api/setup").json()
    assert status["unpriced_providers"] == ["firecrawl"]
    pricing_step = next(s for s in status["steps"] if s["id"] == "pricing")
    assert pricing_step["done"] is False
    assert "firecrawl" in pricing_step["hint"]


def test_summary_compare_reports_deltas_against_the_previous_window(client):
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        provider = db.scalar(select(Provider).where(Provider.slug == "openai"))
        for days_ago, cost in [(2, 4.0), (9, 1.0)]:  # this week: $4, last week: $1
            db.add(
                RequestEvent(
                    created_at=now - timedelta(days=days_ago),
                    provider_id=provider.id,
                    model="gpt-4o",
                    cost_usd=cost,
                    cost_source="catalog",
                    status_code=200,
                )
            )
    data = client.get("/api/analytics/summary", params={"days": 7, "compare": "true"}).json()
    assert data["cost_usd"] == 4.0
    assert data["previous"]["cost_usd"] == 1.0
    assert data["delta_pct"]["cost_usd"] == 300.0
    assert data["delta_pct"]["requests"] == 0.0


def test_summary_compare_returns_null_delta_when_there_is_no_baseline(client):
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        provider = db.scalar(select(Provider).where(Provider.slug == "openai"))
        db.add(
            RequestEvent(
                created_at=now - timedelta(days=1),
                provider_id=provider.id,
                model="gpt-4o",
                cost_usd=2.0,
                cost_source="catalog",
                status_code=200,
            )
        )
    data = client.get("/api/analytics/summary", params={"days": 7, "compare": "true"}).json()
    assert data["delta_pct"]["cost_usd"] is None


def test_summary_without_compare_is_unchanged(client):
    data = client.get("/api/analytics/summary").json()
    assert "previous" not in data
    assert "delta_pct" not in data


def test_comparison_respects_filters(session):
    """The previous-period figure must be filtered the same way as the current one."""
    from openmetric.models import Project

    now = datetime.now(timezone.utc)
    alpha = Project(slug="alpha", name="Alpha")
    beta = Project(slug="beta", name="Beta")
    provider = Provider(slug="openai", name="OpenAI", base_url="https://x")
    session.add_all([alpha, beta, provider])
    session.flush()
    for project, days_ago, cost in [(alpha, 1, 1.0), (alpha, 9, 2.0), (beta, 9, 50.0)]:
        session.add(
            RequestEvent(
                created_at=now - timedelta(days=days_ago),
                project_id=project.id,
                provider_id=provider.id,
                cost_usd=cost,
                cost_source="catalog",
                status_code=200,
            )
        )
    session.flush()
    data = analytics.summary_with_comparison(session, analytics.Filters(days=7, project="alpha"))
    assert data["previous"]["cost_usd"] == 2.0  # beta's $50 must not leak in
