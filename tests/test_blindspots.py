from datetime import datetime, timedelta, timezone

import pytest

from openmetric import analytics, blindspots
from openmetric.db import init_db, session_scope
from openmetric.models import Credential, Project, Provider, RequestEvent


def _findings(session, **kwargs):
    result = blindspots.run_all(session, analytics.Filters(days=30), **kwargs)
    return {f["id"]: f for f in result["findings"]}


@pytest.fixture
def db():
    init_db()
    with session_scope() as session:
        yield session


def _provider(db, slug="openrouter"):
    from sqlalchemy import select

    provider = db.scalar(select(Provider).where(Provider.slug == slug))
    if provider is None:
        provider = Provider(slug=slug, name=slug, base_url="https://x")
        db.add(provider)
        db.flush()
    return provider


def test_untagged_traffic_is_flagged(db):
    provider = _provider(db)
    project = Project(slug="tagged", name="Tagged")
    db.add(project)
    db.flush()
    for i in range(10):
        db.add(
            RequestEvent(
                project_id=project.id if i < 5 else None,
                provider_id=provider.id,
                cost_usd=1.0,
                cost_source="catalog",
                input_tokens=10,
                status_code=200,
            )
        )
    db.flush()

    finding = _findings(db)["untagged_traffic"]
    assert finding["severity"] == "high"
    assert finding["evidence"]["untagged_requests"] == 5
    assert finding["evidence"]["untagged_cost_usd"] == 5.0


def test_unpriced_spend_is_flagged_with_the_offending_models(db):
    provider = _provider(db)
    for _ in range(4):
        db.add(
            RequestEvent(
                provider_id=provider.id,
                model="mystery/model-x",
                cost_usd=0.0,
                cost_source="unknown",
                input_tokens=100,
                status_code=200,
            )
        )
    db.flush()

    finding = _findings(db)["unpriced_spend"]
    assert finding["metric"] == 4
    assert finding["evidence"]["items"][0]["model"] == "mystery/model-x"


def test_clean_data_produces_no_untagged_or_unpriced_finding(db):
    provider = _provider(db)
    project = Project(slug="clean", name="Clean")
    db.add(project)
    db.flush()
    for _ in range(5):
        db.add(
            RequestEvent(
                project_id=project.id,
                provider_id=provider.id,
                model="gpt-4o",
                cost_usd=0.5,
                cost_source="catalog",
                input_tokens=10,
                status_code=200,
            )
        )
    db.flush()

    found = _findings(db)
    assert "untagged_traffic" not in found
    assert "unpriced_spend" not in found


def test_idle_credentials_are_flagged(db):
    provider = _provider(db)
    old = datetime.now(timezone.utc) - timedelta(days=90)
    db.add_all(
        [
            Credential(
                provider_id=provider.id,
                label="forgotten-key",
                encrypted_secret="x",
                key_hint="...1111",
                key_fingerprint="a",
                last_used_at=old,
            ),
            Credential(
                provider_id=provider.id,
                label="never-used-key",
                encrypted_secret="x",
                key_hint="...2222",
                key_fingerprint="b",
            ),
        ]
    )
    db.flush()

    finding = _findings(db)["idle_credentials"]
    labels = {c["credential"] for c in finding["evidence"]["credentials"]}
    assert labels == {"forgotten-key", "never-used-key"}


def test_recently_used_credential_is_not_flagged(db):
    provider = _provider(db)
    db.add(
        Credential(
            provider_id=provider.id,
            label="active-key",
            encrypted_secret="x",
            key_hint="...3333",
            key_fingerprint="c",
            last_used_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    assert "idle_credentials" not in _findings(db)


def test_shared_credential_across_projects_is_reported(db):
    provider = _provider(db)
    key = Credential(
        provider_id=provider.id,
        label="one-key-many-projects",
        encrypted_secret="x",
        key_hint="...4444",
        key_fingerprint="d",
        last_used_at=datetime.now(timezone.utc),
    )
    projects = [Project(slug=f"p{i}", name=f"P{i}") for i in range(2)]
    db.add_all([key, *projects])
    db.flush()
    for project in projects:
        db.add(
            RequestEvent(
                project_id=project.id,
                provider_id=provider.id,
                credential_id=key.id,
                cost_usd=1.0,
                cost_source="catalog",
                status_code=200,
            )
        )
    db.flush()

    finding = _findings(db)["shared_credentials"]
    assert finding["evidence"]["credentials"][0]["projects"] == 2


def test_error_burn_counts_money_spent_on_failures(db):
    provider = _provider(db)
    for i in range(10):
        failed = i < 3
        db.add(
            RequestEvent(
                provider_id=provider.id,
                cost_usd=1.0,
                cost_source="catalog",
                status_code=429 if failed else 200,
                ok=not failed,
                model="gpt-4o",
            )
        )
    db.flush()

    finding = _findings(db)["error_burn"]
    assert finding["metric"] == 3.0
    assert finding["evidence"]["error_rate_pct"] == 30.0


def test_budget_overrun_is_high_severity(db):
    provider = _provider(db)
    project = Project(slug="spendy", name="Spendy", monthly_budget_usd=1.0)
    db.add(project)
    db.flush()
    db.add(
        RequestEvent(
            project_id=project.id,
            provider_id=provider.id,
            cost_usd=25.0,
            cost_source="catalog",
            model="gpt-4o",
            status_code=200,
        )
    )
    db.flush()

    finding = _findings(db)["budget_overrun"]
    assert finding["severity"] == "high"


def test_empty_database_produces_no_findings(db):
    assert _findings(db) == {}


def test_findings_are_sorted_by_severity(db):
    provider = _provider(db)
    project = Project(slug="mixed", name="Mixed", monthly_budget_usd=0.5)
    db.add(project)
    db.flush()
    for i in range(20):
        db.add(
            RequestEvent(
                project_id=project.id if i % 2 else None,
                provider_id=provider.id,
                model="mystery/x",
                cost_usd=1.0 if i % 2 else 0.0,
                cost_source="catalog" if i % 2 else "unknown",
                status_code=500 if i < 4 else 200,
                ok=i >= 4,
            )
        )
    db.flush()

    result = blindspots.run_all(db, analytics.Filters(days=30))
    order = [blindspots.SEVERITY_ORDER[f["severity"]] for f in result["findings"]]
    assert order == sorted(order)
    assert result["counts"]["high"] >= 1
