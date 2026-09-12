"""Blindspots: the things a normal usage dashboard will not tell you.

A dashboard answers "what did I spend". These checks answer the more useful
question - "what is my spend data *wrong* about, and what am I paying for
without noticing". Each check returns a finding with the evidence behind it,
so nothing here is a vibe: you can click through to the events.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .analytics import Filters, group, summary
from .models import Credential, Project, Provider, RequestEvent

HIGH, WARN, INFO = "high", "warn", "info"


@dataclass
class Finding:
    id: str
    severity: str
    title: str
    detail: str
    action: str
    metric: float = 0.0
    unit: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def untagged_traffic(session: Session, filters: Filters) -> Finding | None:
    """Calls that arrived with no project or no use case cannot be attributed."""
    since, until = filters.window()
    base = select(
        func.count(RequestEvent.id),
        func.coalesce(func.sum(RequestEvent.cost_usd), 0.0),
        func.coalesce(func.sum(case((RequestEvent.project_id.is_(None), 1), else_=0)), 0),
        func.coalesce(
            func.sum(case((RequestEvent.project_id.is_(None), RequestEvent.cost_usd), else_=0.0)),
            0.0,
        ),
        func.coalesce(func.sum(case((RequestEvent.use_case_id.is_(None), 1), else_=0)), 0),
    ).where(RequestEvent.created_at >= since, RequestEvent.created_at <= until)
    total, total_cost, no_project, no_project_cost, no_use_case = session.execute(base).one()
    if not total or not no_project:
        return None

    share = _pct(no_project, total)
    severity = HIGH if share > 25 else WARN if share > 5 else INFO
    return Finding(
        id="untagged_traffic",
        severity=severity,
        title=f"{share}% of calls are not attributed to a project",
        detail=(
            f"{no_project:,} of {total:,} calls (${no_project_cost:.2f} of "
            f"${total_cost:.2f}) arrived without a project tag, and {no_use_case:,} "
            f"without a use case. That spend is real but unassignable."
        ),
        action=(
            "Issue a virtual key per project (`openmetric vkey create --project ...`) "
            "or send the X-OpenMetric-Project header."
        ),
        metric=no_project_cost,
        unit="usd",
        evidence={
            "untagged_requests": int(no_project),
            "untagged_cost_usd": round(float(no_project_cost), 4),
            "requests_without_use_case": int(no_use_case),
            "share_pct": share,
        },
    )


def unpriced_spend(session: Session, filters: Filters) -> Finding | None:
    """Calls we counted but could not price. The most dangerous blindspot: it reads as $0."""
    since, until = filters.window()
    stmt = (
        select(
            RequestEvent.model,
            Provider.slug,
            func.count(RequestEvent.id).label("requests"),
            func.coalesce(func.sum(RequestEvent.input_tokens + RequestEvent.output_tokens), 0),
        )
        .outerjoin(Provider, RequestEvent.provider_id == Provider.id)
        .where(
            RequestEvent.created_at >= since,
            RequestEvent.created_at <= until,
            RequestEvent.cost_source == "unknown",
            RequestEvent.ok.is_(True),
        )
        .group_by(RequestEvent.model, Provider.slug)
        .order_by(func.count(RequestEvent.id).desc())
        .limit(15)
    )
    rows = session.execute(stmt).all()
    if not rows:
        return None

    unpriced = sum(int(r.requests) for r in rows)
    total = (
        session.scalar(
            select(func.count(RequestEvent.id)).where(
                RequestEvent.created_at >= since, RequestEvent.created_at <= until
            )
        )
        or 0
    )
    share = _pct(unpriced, total)
    return Finding(
        id="unpriced_spend",
        severity=HIGH if share > 20 else WARN,
        title=f"{unpriced:,} calls have no price attached",
        detail=(
            f"{share}% of calls in this window show as $0 because no rate is known "
            "for the model or provider. Your real total is higher than the dashboard says."
        ),
        action=(
            "Add rates: `openmetric price set <provider> --per-request 0.002` for per-call "
            "APIs, or `openmetric price set --model <name> --input X --output Y` per 1M tokens."
        ),
        metric=float(unpriced),
        unit="requests",
        evidence={
            "share_pct": share,
            "items": [
                {
                    "model": r.model or "(none)",
                    "provider": r.slug or "(unknown)",
                    "requests": int(r.requests),
                    "tokens": int(r[3] or 0),
                }
                for r in rows
            ],
        },
    )


def idle_credentials(session: Session, days: int = 30) -> Finding | None:
    """Keys you are still holding (and possibly still paying for) but not calling."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = []
    for credential in session.scalars(select(Credential).where(Credential.active.is_(True))):
        last_used = credential.last_used_at
        if last_used is not None and last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=timezone.utc)
        if last_used is None or last_used < cutoff:
            stale.append(
                {
                    "credential": credential.label,
                    "provider": credential.provider.slug if credential.provider else "?",
                    "key_hint": credential.key_hint,
                    "last_used_at": last_used.isoformat() if last_used else None,
                }
            )
    if not stale:
        return None
    return Finding(
        id="idle_credentials",
        severity=WARN,
        title=f"{len(stale)} active key(s) unused for {days}+ days",
        detail=(
            "Unused keys are still live credentials: they can leak, and on metered plans "
            "they can still carry a subscription. Rotate or deactivate what you do not use."
        ),
        action="`openmetric key deactivate <label>` or revoke it at the provider.",
        metric=float(len(stale)),
        unit="keys",
        evidence={"credentials": stale},
    )


def shared_credentials(session: Session, filters: Filters) -> Finding | None:
    """One key serving several projects - the provider bill can never be split."""
    since, until = filters.window()
    stmt = (
        select(
            Credential.label,
            Provider.slug,
            func.count(func.distinct(RequestEvent.project_id)).label("projects"),
            func.coalesce(func.sum(RequestEvent.cost_usd), 0.0).label("cost"),
        )
        .join(Credential, RequestEvent.credential_id == Credential.id)
        .outerjoin(Provider, Credential.provider_id == Provider.id)
        .where(RequestEvent.created_at >= since, RequestEvent.created_at <= until)
        .group_by(Credential.label, Provider.slug)
        .having(func.count(func.distinct(RequestEvent.project_id)) > 1)
        .order_by(func.coalesce(func.sum(RequestEvent.cost_usd), 0.0).desc())
    )
    rows = session.execute(stmt).all()
    if not rows:
        return None
    return Finding(
        id="shared_credentials",
        severity=INFO,
        title=f"{len(rows)} key(s) shared across projects",
        detail=(
            "OpenMetric can still attribute these calls, but the provider's own invoice "
            "cannot - it only sees one key. Reconciling your bill will need this table."
        ),
        action="Issue one key per project at the provider if you need invoice-level separation.",
        metric=float(len(rows)),
        unit="keys",
        evidence={
            "credentials": [
                {
                    "credential": r.label,
                    "provider": r.slug,
                    "projects": int(r.projects),
                    "cost_usd": round(float(r.cost), 4),
                }
                for r in rows
            ]
        },
    )


def error_burn(session: Session, filters: Filters) -> Finding | None:
    """Money and latency spent on calls that failed."""
    since, until = filters.window()
    row = session.execute(
        select(
            func.count(RequestEvent.id),
            func.coalesce(func.sum(case((RequestEvent.ok.is_(False), 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((RequestEvent.ok.is_(False), RequestEvent.cost_usd), else_=0.0)), 0.0
            ),
        ).where(RequestEvent.created_at >= since, RequestEvent.created_at <= until)
    ).one()
    total, errors, wasted = int(row[0]), int(row[1]), float(row[2])
    if not total or not errors:
        return None
    rate = _pct(errors, total)
    if rate < 1 and wasted < 0.01:
        return None

    by_status = session.execute(
        select(RequestEvent.status_code, func.count(RequestEvent.id))
        .where(
            RequestEvent.created_at >= since,
            RequestEvent.created_at <= until,
            RequestEvent.ok.is_(False),
        )
        .group_by(RequestEvent.status_code)
        .order_by(func.count(RequestEvent.id).desc())
        .limit(8)
    ).all()
    return Finding(
        id="error_burn",
        severity=HIGH if rate > 10 else WARN,
        title=f"{rate}% of calls failed (${wasted:.2f} burned)",
        detail=(
            f"{errors:,} of {total:,} calls returned an error. Failed LLM calls are often "
            "still billed for input tokens, and retries multiply that."
        ),
        action="Check the status breakdown below; 429s mean rate limits, 401s mean a bad key.",
        metric=wasted,
        unit="usd",
        evidence={
            "error_rate_pct": rate,
            "failed_requests": errors,
            "by_status": [{"status": int(s or 0), "requests": int(c)} for s, c in by_status],
        },
    )


def dormant_projects(session: Session, filters: Filters) -> Finding | None:
    """Projects configured but silent in this window."""
    active = {g["label"] for g in group(session, filters, by="project", limit=500)}
    dormant = [
        p.slug
        for p in session.scalars(select(Project).where(Project.archived.is_(False)))
        if p.slug not in active
    ]
    if not dormant:
        return None
    return Finding(
        id="dormant_projects",
        severity=INFO,
        title=f"{len(dormant)} project(s) had no traffic",
        detail="No calls in this window. Archive them, or find out what silently stopped working.",
        action="`openmetric project archive <slug>` to hide a finished project.",
        metric=float(len(dormant)),
        unit="projects",
        evidence={"projects": dormant},
    )


def concentration_risk(session: Session, filters: Filters) -> Finding | None:
    """One provider or model carrying most of the spend."""
    totals = summary(session, filters)
    if totals["cost_usd"] <= 0:
        return None
    top = group(session, filters, by="provider", limit=1)
    if not top:
        return None
    leader = top[0]
    share = _pct(leader["cost_usd"], totals["cost_usd"])
    if share < 80:
        return None
    return Finding(
        id="concentration_risk",
        severity=INFO,
        title=f"{share}% of spend goes through '{leader['label']}'",
        detail=(
            "A price change, outage or account suspension at one provider would hit nearly "
            "everything you run. Worth knowing before it happens, not after."
        ),
        action="Keep a second provider configured and a fallback path tested.",
        metric=share,
        unit="pct",
        evidence={"provider": leader["label"], "cost_usd": leader["cost_usd"], "share_pct": share},
    )


def spend_trend(session: Session, filters: Filters) -> Finding | None:
    """This window against the one before it."""
    since, until = filters.window()
    span = until - since
    current = summary(session, Filters(since=since, until=until))["cost_usd"]
    previous = summary(session, Filters(since=since - span, until=since))["cost_usd"]
    if previous <= 0 or current <= 0:
        return None
    change = (current - previous) / previous * 100
    if abs(change) < 50:
        return None
    direction = "up" if change > 0 else "down"
    return Finding(
        id="spend_trend",
        severity=WARN if change > 0 else INFO,
        title=f"Spend is {direction} {abs(round(change))}% vs the previous period",
        detail=f"${previous:.2f} -> ${current:.2f} over an equivalent window.",
        action="Group by model and use case to see which one moved.",
        metric=round(change, 1),
        unit="pct",
        evidence={"previous_usd": round(previous, 4), "current_usd": round(current, 4)},
    )


def budget_overruns(session: Session) -> Finding | None:
    """Projects past the budget you set for them."""
    from .analytics import budget_status

    over = [b for b in budget_status(session, Filters()) if b["over_budget"]]
    if not over:
        return None
    return Finding(
        id="budget_overrun",
        severity=HIGH,
        title=f"{len(over)} project(s) over budget this month",
        detail=", ".join(
            f"{b['project']}: ${b['month_to_date_usd']:.2f} of ${b['monthly_budget_usd']:.2f}"
            for b in over
        ),
        action="Raise the budget or move that workload to a cheaper model.",
        metric=float(len(over)),
        unit="projects",
        evidence={"projects": over},
    )


def latency_tail(session: Session, filters: Filters) -> Finding | None:
    """A p95 far above the average means a slow tail your users feel."""
    totals = summary(session, filters)
    p95, avg = totals["p95_latency_ms"], totals["avg_latency_ms"]
    if totals["requests"] < 20 or not avg or p95 < 3 * avg or p95 < 5000:
        return None
    return Finding(
        id="latency_tail",
        severity=WARN,
        title=f"p95 latency is {p95:,}ms against a {avg:,.0f}ms average",
        detail="A long tail like this usually means retries, rate limiting, or one slow model.",
        action="Group by model and by provider with order_by=latency to find the source.",
        metric=float(p95),
        unit="ms",
        evidence={"p95_latency_ms": p95, "avg_latency_ms": avg, "requests": totals["requests"]},
    )


SEVERITY_ORDER = {HIGH: 0, WARN: 1, INFO: 2}


def run_all(
    session: Session, filters: Filters | None = None, idle_days: int = 30
) -> dict[str, Any]:
    """Every check, most serious first."""
    filters = filters or Filters()
    findings = [
        untagged_traffic(session, filters),
        unpriced_spend(session, filters),
        error_burn(session, filters),
        budget_overruns(session),
        spend_trend(session, filters),
        idle_credentials(session, idle_days),
        shared_credentials(session, filters),
        latency_tail(session, filters),
        concentration_risk(session, filters),
        dormant_projects(session, filters),
    ]
    found = [f for f in findings if f is not None]
    found.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.metric))
    since, until = filters.window()
    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "counts": {
            "high": sum(1 for f in found if f.severity == HIGH),
            "warn": sum(1 for f in found if f.severity == WARN),
            "info": sum(1 for f in found if f.severity == INFO),
        },
        "findings": [f.to_dict() for f in found],
    }
