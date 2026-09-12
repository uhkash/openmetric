from datetime import datetime, timedelta, timezone

import pytest

from openmetric import analytics
from openmetric.db import init_db, session_scope
from openmetric.models import Credential, Project, Provider, RequestEvent, UseCase


@pytest.fixture
def populated():
    """Two projects, two providers, known costs - so every total is checkable by hand."""
    init_db()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        alpha = Project(slug="alpha", name="Alpha", monthly_budget_usd=1.0)
        beta = Project(slug="beta", name="Beta")
        summary = UseCase(slug="summary", name="Summary")
        scraping = UseCase(slug="scraping", name="Scraping")
        openrouter = Provider(slug="openrouter", name="OpenRouter", base_url="https://x")
        firecrawl = Provider(
            slug="firecrawl", name="Firecrawl", base_url="https://y", kind="scrape"
        )
        db.add_all([alpha, beta, summary, scraping, openrouter, firecrawl])
        db.flush()

        key = Credential(
            provider_id=openrouter.id,
            label="shared-key",
            encrypted_secret="x",
            key_hint="...1234",
            key_fingerprint="abc",
        )
        db.add(key)
        db.flush()

        rows = [
            (alpha, summary, openrouter, "gpt-4o", 2.0, 100, 50, 200, 1000, key),
            (alpha, summary, openrouter, "gpt-4o", 3.0, 200, 60, 200, 2000, key),
            (beta, scraping, firecrawl, "", 0.0, 0, 0, 200, 500, None),
            (beta, summary, openrouter, "gpt-4o", 1.0, 50, 25, 500, 9000, key),
            (None, None, openrouter, "mystery-model", 0.0, 10, 5, 200, 300, key),
        ]
        for i, (proj, uc, prov, model, cost, tin, tout, status, latency, cred) in enumerate(rows):
            db.add(
                RequestEvent(
                    created_at=now - timedelta(days=i),
                    project_id=proj.id if proj else None,
                    use_case_id=uc.id if uc else None,
                    provider_id=prov.id,
                    credential_id=cred.id if cred else None,
                    model=model,
                    cost_usd=cost,
                    cost_source="catalog" if cost else "unknown",
                    input_tokens=tin,
                    output_tokens=tout,
                    status_code=status,
                    ok=status < 400,
                    latency_ms=latency,
                )
            )
    with session_scope() as db:
        yield db


def test_summary_totals(populated):
    totals = analytics.summary(populated, analytics.Filters(days=30))
    assert totals["requests"] == 5
    assert totals["cost_usd"] == 6.0
    assert totals["total_tokens"] == 500
    assert totals["errors"] == 1
    assert totals["error_rate"] == 0.2
    assert totals["unpriced_requests"] == 2


def test_group_by_project_puts_untagged_traffic_in_its_own_row(populated):
    rows = {r["label"]: r for r in analytics.group(populated, analytics.Filters(), by="project")}
    assert rows["alpha"]["cost_usd"] == 5.0
    assert rows["beta"]["cost_usd"] == 1.0
    assert rows["(untagged)"]["requests"] == 1


def test_group_by_other_dimensions(populated):
    by_provider = {
        r["label"]: r for r in analytics.group(populated, analytics.Filters(), by="provider")
    }
    assert by_provider["openrouter"]["requests"] == 4
    assert by_provider["firecrawl"]["requests"] == 1

    by_model = {r["label"]: r for r in analytics.group(populated, analytics.Filters(), by="model")}
    assert by_model["gpt-4o"]["cost_usd"] == 6.0


def test_filters_narrow_the_window(populated):
    alpha_only = analytics.summary(populated, analytics.Filters(project="alpha"))
    assert alpha_only["requests"] == 2
    assert alpha_only["cost_usd"] == 5.0

    errors_only = analytics.summary(populated, analytics.Filters(only_errors=True))
    assert errors_only["requests"] == 1


def test_matrix_cross_tab_totals_agree_with_the_grand_total(populated):
    result = analytics.matrix(populated, analytics.Filters(), "project", "provider", "cost_usd")
    assert result["grand_total"] == 6.0
    assert sum(result["row_totals"]) == pytest.approx(result["grand_total"])
    row = result["rows"].index("alpha")
    col = result["cols"].index("openrouter")
    assert result["cells"][row][col] == 5.0


def test_timeseries_buckets_by_day(populated):
    series = analytics.timeseries(populated, analytics.Filters(days=30), bucket="day")
    points = series["series"][0]["points"]
    assert len(points) == 5
    assert sum(p["cost_usd"] for p in points) == pytest.approx(6.0)


def test_timeseries_split_by_dimension(populated):
    series = analytics.timeseries(populated, analytics.Filters(), bucket="day", by="project")
    labels = {s["label"] for s in series["series"]}
    assert {"alpha", "beta"} <= labels


def test_percentile_is_between_min_and_max(populated):
    p95 = analytics.percentile_latency(populated, analytics.Filters(), 0.95)
    assert 300 <= p95 <= 9000


def test_budget_status_flags_overrun(populated):
    budgets = {b["project"]: b for b in analytics.budget_status(populated, analytics.Filters())}
    assert budgets["alpha"]["over_budget"] is True
    assert budgets["beta"]["monthly_budget_usd"] is None


def test_unknown_dimension_is_rejected(populated):
    with pytest.raises(ValueError, match="Unknown dimension"):
        analytics.group(populated, analytics.Filters(), by="nonsense")


def test_recent_events_are_newest_first(populated):
    events = analytics.recent_events(populated, analytics.Filters(), limit=10)
    stamps = [e["created_at"] for e in events]
    assert stamps == sorted(stamps, reverse=True)
