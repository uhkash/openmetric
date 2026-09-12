"""Slice and dice. One event table, many angles.

Every question the dashboard asks - "what did this project cost last week",
"which use case burns the most tokens", "which of my four OpenRouter keys is
actually being used" - is the same query with a different ``group_by``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Float, case, func, select
from sqlalchemy.orm import Session

from .models import Credential, Project, Provider, RequestEvent, UseCase

# group_by name -> (column on RequestEvent, joined label column, fallback label)
DIMENSIONS: dict[str, tuple[Any, Any, str]] = {
    "project": (RequestEvent.project_id, Project.slug, "(untagged)"),
    "use_case": (RequestEvent.use_case_id, UseCase.slug, "(untagged)"),
    "provider": (RequestEvent.provider_id, Provider.slug, "(unknown)"),
    "credential": (RequestEvent.credential_id, Credential.label, "(unknown)"),
    "model": (RequestEvent.model, None, "(none)"),
    "operation": (RequestEvent.operation, None, "(none)"),
    "cost_source": (RequestEvent.cost_source, None, "unknown"),
    "status": (RequestEvent.status_code, None, "0"),
}

_JOINS = {
    "project": (Project, RequestEvent.project_id == Project.id),
    "use_case": (UseCase, RequestEvent.use_case_id == UseCase.id),
    "provider": (Provider, RequestEvent.provider_id == Provider.id),
    "credential": (Credential, RequestEvent.credential_id == Credential.id),
}


@dataclass
class Filters:
    """Common filter set shared by every analytics call."""

    days: int = 30
    since: datetime | None = None
    until: datetime | None = None
    project: str | None = None
    use_case: str | None = None
    provider: str | None = None
    model: str | None = None
    credential: str | None = None
    only_errors: bool = False
    exclude_errors: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def window(self) -> tuple[datetime, datetime]:
        until = self.until or datetime.now(timezone.utc)
        since = self.since or (until - timedelta(days=self.days))
        return since, until


def _apply(stmt, filters: Filters):
    since, until = filters.window()
    stmt = stmt.where(RequestEvent.created_at >= since, RequestEvent.created_at <= until)
    if filters.project:
        stmt = stmt.where(
            RequestEvent.project_id.in_(select(Project.id).where(Project.slug == filters.project))
        )
    if filters.use_case:
        stmt = stmt.where(
            RequestEvent.use_case_id.in_(select(UseCase.id).where(UseCase.slug == filters.use_case))
        )
    if filters.provider:
        stmt = stmt.where(
            RequestEvent.provider_id.in_(
                select(Provider.id).where(Provider.slug == filters.provider)
            )
        )
    if filters.credential:
        stmt = stmt.where(
            RequestEvent.credential_id.in_(
                select(Credential.id).where(Credential.label == filters.credential)
            )
        )
    if filters.model:
        stmt = stmt.where(RequestEvent.model == filters.model)
    if filters.only_errors:
        stmt = stmt.where(RequestEvent.ok.is_(False))
    if filters.exclude_errors:
        stmt = stmt.where(RequestEvent.ok.is_(True))
    return stmt


_METRICS = [
    func.count(RequestEvent.id).label("requests"),
    func.coalesce(func.sum(RequestEvent.cost_usd), 0.0).label("cost_usd"),
    func.coalesce(func.sum(RequestEvent.input_tokens), 0).label("input_tokens"),
    func.coalesce(func.sum(RequestEvent.output_tokens), 0).label("output_tokens"),
    func.coalesce(func.sum(RequestEvent.cached_tokens), 0).label("cached_tokens"),
    func.coalesce(func.sum(RequestEvent.units), 0.0).label("units"),
    func.coalesce(func.avg(RequestEvent.latency_ms), 0.0).label("avg_latency_ms"),
    func.coalesce(func.max(RequestEvent.latency_ms), 0).label("max_latency_ms"),
    func.sum(case((RequestEvent.ok.is_(False), 1), else_=0)).label("errors"),
    func.sum(case((RequestEvent.cost_source == "unknown", RequestEvent.id * 0 + 1), else_=0)).label(
        "unpriced_requests"
    ),
]


def _row_to_dict(row: Any, label: str | None = None) -> dict[str, Any]:
    data = {
        "requests": int(row.requests or 0),
        "cost_usd": round(float(row.cost_usd or 0.0), 6),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cached_tokens": int(row.cached_tokens or 0),
        "units": round(float(row.units or 0.0), 3),
        "avg_latency_ms": round(float(row.avg_latency_ms or 0.0), 1),
        "max_latency_ms": int(row.max_latency_ms or 0),
        "errors": int(row.errors or 0),
        "unpriced_requests": int(row.unpriced_requests or 0),
    }
    data["total_tokens"] = data["input_tokens"] + data["output_tokens"]
    data["error_rate"] = round(data["errors"] / data["requests"], 4) if data["requests"] else 0.0
    if label is not None:
        data["label"] = label
    return data


def summary(session: Session, filters: Filters) -> dict[str, Any]:
    """Headline totals for the current filter window."""
    row = session.execute(_apply(select(*_METRICS), filters)).one()
    data = _row_to_dict(row)

    since, until = filters.window()
    data["window"] = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "days": max((until - since).days, 1),
    }
    data["cost_per_request"] = (
        round(data["cost_usd"] / data["requests"], 6) if data["requests"] else 0.0
    )
    data["p95_latency_ms"] = percentile_latency(session, filters, 0.95)
    return data


def summary_with_comparison(session: Session, filters: Filters) -> dict[str, Any]:
    """Headline totals plus the same window immediately before it, for deltas."""
    current = summary(session, filters)
    since, until = filters.window()
    span = until - since
    previous_filters = Filters(
        since=since - span,
        until=since,
        project=filters.project,
        use_case=filters.use_case,
        provider=filters.provider,
        model=filters.model,
        credential=filters.credential,
        only_errors=filters.only_errors,
        exclude_errors=filters.exclude_errors,
    )
    previous = summary(session, previous_filters)

    def delta(key: str) -> float | None:
        before, now = previous.get(key) or 0, current.get(key) or 0
        if not before:
            return None
        return round((now - before) / before * 100, 1)

    current["previous"] = {
        "requests": previous["requests"],
        "cost_usd": previous["cost_usd"],
        "total_tokens": previous["total_tokens"],
        "error_rate": previous["error_rate"],
        "p95_latency_ms": previous["p95_latency_ms"],
        "unpriced_requests": previous["unpriced_requests"],
    }
    current["delta_pct"] = {
        "requests": delta("requests"),
        "cost_usd": delta("cost_usd"),
        "total_tokens": delta("total_tokens"),
        "error_rate": delta("error_rate"),
        "p95_latency_ms": delta("p95_latency_ms"),
    }
    return current


def percentile_latency(session: Session, filters: Filters, quantile: float = 0.95) -> int:
    """Portable percentile: works the same on SQLite and Postgres."""
    total = session.scalar(_apply(select(func.count(RequestEvent.id)), filters)) or 0
    if total == 0:
        return 0
    offset = min(int(total * quantile), total - 1)
    stmt = _apply(select(RequestEvent.latency_ms), filters).order_by(RequestEvent.latency_ms)
    return int(session.scalars(stmt.offset(offset).limit(1)).first() or 0)


def group(
    session: Session,
    filters: Filters,
    by: str = "project",
    limit: int = 50,
    order_by: str = "cost_usd",
) -> list[dict[str, Any]]:
    """Totals broken down by any dimension."""
    if by not in DIMENSIONS:
        raise ValueError(f"Unknown dimension '{by}'. Try one of: {', '.join(DIMENSIONS)}")
    id_col, label_col, fallback = DIMENSIONS[by]

    selected = [id_col.label("key")] + _METRICS
    if label_col is not None:
        selected.insert(1, label_col.label("label"))

    stmt = select(*selected)
    if by in _JOINS:
        model, condition = _JOINS[by]
        stmt = stmt.outerjoin(model, condition)
    stmt = _apply(stmt, filters).group_by(id_col)
    if label_col is not None:
        stmt = stmt.group_by(label_col)

    sort_column = {
        "cost_usd": func.sum(RequestEvent.cost_usd),
        "requests": func.count(RequestEvent.id),
        "tokens": func.sum(RequestEvent.input_tokens + RequestEvent.output_tokens),
        "errors": func.sum(case((RequestEvent.ok.is_(False), 1), else_=0)),
        "latency": func.avg(RequestEvent.latency_ms),
    }.get(order_by, func.sum(RequestEvent.cost_usd))

    rows = session.execute(stmt.order_by(sort_column.desc()).limit(limit)).all()
    out = []
    for row in rows:
        label = getattr(row, "label", None) or (str(row.key) if row.key not in (None, "") else None)
        entry = _row_to_dict(row, label or fallback)
        entry["key"] = row.key
        entry["dimension"] = by
        out.append(entry)
    return out


def _bucket_column(session: Session, bucket: str):
    """Truncate the timestamp to an hour/day/month, in whichever SQL dialect is in use."""
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "sqlite":
        fmt = {"hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}.get(
            bucket, "%Y-%m-%d"
        )
        return func.strftime(fmt, RequestEvent.created_at).label("bucket")
    unit = bucket if bucket in {"hour", "day", "month"} else "day"
    return func.to_char(
        func.date_trunc(unit, RequestEvent.created_at),
        {"hour": 'YYYY-MM-DD"T"HH24:00', "day": "YYYY-MM-DD", "month": "YYYY-MM"}[unit],
    ).label("bucket")


def timeseries(
    session: Session, filters: Filters, bucket: str = "day", by: str | None = None, limit: int = 8
) -> dict[str, Any]:
    """Cost/requests over time, optionally split into series by a dimension."""
    bucket_col = _bucket_column(session, bucket)

    if by is None:
        stmt = _apply(select(bucket_col, *_METRICS), filters).group_by("bucket").order_by("bucket")
        points = [{**_row_to_dict(r), "bucket": r.bucket} for r in session.execute(stmt).all()]
        return {"bucket": bucket, "series": [{"label": "all", "points": points}]}

    top = [g["label"] for g in group(session, filters, by=by, limit=limit)]
    id_col, label_col, fallback = DIMENSIONS[by]
    selected = [bucket_col, id_col.label("key")] + _METRICS
    if label_col is not None:
        selected.insert(2, label_col.label("label"))

    stmt = select(*selected)
    if by in _JOINS:
        model, condition = _JOINS[by]
        stmt = stmt.outerjoin(model, condition)
    stmt = _apply(stmt, filters).group_by("bucket", id_col)
    if label_col is not None:
        stmt = stmt.group_by(label_col)

    series: dict[str, list[dict]] = {name: [] for name in top}
    for row in session.execute(stmt.order_by("bucket")).all():
        label = getattr(row, "label", None) or (
            str(row.key) if row.key not in (None, "") else fallback
        )
        if label in series:
            series[label].append({**_row_to_dict(row), "bucket": row.bucket})
    return {
        "bucket": bucket,
        "dimension": by,
        "series": [{"label": k, "points": v} for k, v in series.items()],
    }


def matrix(
    session: Session,
    filters: Filters,
    rows_by: str = "project",
    cols_by: str = "provider",
    metric: str = "cost_usd",
    limit: int = 20,
) -> dict[str, Any]:
    """Cross-tab, e.g. spend per project x provider. The 'different angles' view."""
    for dimension in (rows_by, cols_by):
        if dimension not in DIMENSIONS:
            raise ValueError(f"Unknown dimension '{dimension}'")

    row_id, row_label, row_fallback = DIMENSIONS[rows_by]
    col_id, col_label, col_fallback = DIMENSIONS[cols_by]

    metric_col = {
        "cost_usd": func.coalesce(func.sum(RequestEvent.cost_usd), 0.0),
        "requests": func.count(RequestEvent.id),
        "tokens": func.coalesce(
            func.sum(RequestEvent.input_tokens + RequestEvent.output_tokens), 0
        ),
        "errors": func.coalesce(func.sum(case((RequestEvent.ok.is_(False), 1), else_=0)), 0),
        "avg_latency_ms": func.coalesce(func.avg(RequestEvent.latency_ms), 0.0).cast(Float),
    }.get(metric, func.coalesce(func.sum(RequestEvent.cost_usd), 0.0))

    selected = [row_id.label("row_key"), col_id.label("col_key"), metric_col.label("value")]
    group_cols = [row_id, col_id]
    if row_label is not None:
        selected.append(row_label.label("row_label"))
        group_cols.append(row_label)
    if col_label is not None:
        selected.append(col_label.label("col_label"))
        group_cols.append(col_label)

    stmt = select(*selected)
    seen = set()
    for dimension in (rows_by, cols_by):
        if dimension in _JOINS and dimension not in seen:
            model, condition = _JOINS[dimension]
            stmt = stmt.outerjoin(model, condition)
            seen.add(dimension)
    stmt = _apply(stmt, filters).group_by(*group_cols)

    cells: dict[tuple[str, str], float] = {}
    row_totals: dict[str, float] = {}
    col_totals: dict[str, float] = {}
    for record in session.execute(stmt).all():
        r_label = getattr(record, "row_label", None) or (
            str(record.row_key) if record.row_key not in (None, "") else row_fallback
        )
        c_label = getattr(record, "col_label", None) or (
            str(record.col_key) if record.col_key not in (None, "") else col_fallback
        )
        value = round(float(record.value or 0), 6)
        cells[(r_label, c_label)] = cells.get((r_label, c_label), 0.0) + value
        row_totals[r_label] = row_totals.get(r_label, 0.0) + value
        col_totals[c_label] = col_totals.get(c_label, 0.0) + value

    row_names = [k for k, _ in sorted(row_totals.items(), key=lambda kv: -kv[1])][:limit]
    col_names = [k for k, _ in sorted(col_totals.items(), key=lambda kv: -kv[1])][:limit]
    return {
        "metric": metric,
        "rows_by": rows_by,
        "cols_by": cols_by,
        "rows": row_names,
        "cols": col_names,
        "cells": [[round(cells.get((r, c), 0.0), 6) for c in col_names] for r in row_names],
        "row_totals": [round(row_totals[r], 6) for r in row_names],
        "col_totals": [round(col_totals[c], 6) for c in col_names],
        "grand_total": round(sum(row_totals.values()), 6),
    }


def recent_events(session: Session, filters: Filters, limit: int = 100) -> list[dict[str, Any]]:
    """The raw tail, for when an aggregate looks wrong."""
    stmt = (
        _apply(select(RequestEvent), filters)
        .order_by(RequestEvent.created_at.desc())
        .limit(min(limit, 1000))
    )
    out = []
    for event in session.scalars(stmt):
        out.append(
            {
                "id": event.id,
                "created_at": event.created_at.isoformat(),
                "project": event.project.slug if event.project else None,
                "use_case": event.use_case.slug if event.use_case else None,
                "provider": event.provider.slug if event.provider else None,
                "credential": event.credential.label if event.credential else None,
                "model": event.model,
                "operation": event.operation,
                "status_code": event.status_code,
                "ok": event.ok,
                "latency_ms": event.latency_ms,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "units": event.units,
                "cost_usd": round(event.cost_usd, 6),
                "cost_source": event.cost_source,
                "streamed": event.streamed,
            }
        )
    return out


def budget_status(session: Session, filters: Filters) -> list[dict[str, Any]]:
    """Month-to-date spend against each project's budget."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_filters = Filters(since=month_start, until=now)
    spend = {
        g["label"]: g["cost_usd"] for g in group(session, month_filters, by="project", limit=500)
    }

    out = []
    for project in session.scalars(select(Project).where(Project.archived.is_(False))):
        used = spend.get(project.slug, 0.0)
        budget = project.monthly_budget_usd
        out.append(
            {
                "project": project.slug,
                "month_to_date_usd": round(used, 4),
                "monthly_budget_usd": budget,
                "used_pct": round(used / budget * 100, 1) if budget else None,
                "over_budget": bool(budget and used > budget),
            }
        )
    return sorted(out, key=lambda item: -item["month_to_date_usd"])
