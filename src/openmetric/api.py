"""Management + analytics API. Everything the dashboard uses is here."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import analytics, blindspots, crypto, pricing
from .config import get_settings
from .db import get_db
from .models import Credential, Project, Provider, RequestEvent, UseCase, VirtualKey
from .schemas import (
    CredentialIn,
    CredentialOut,
    ProjectIn,
    ProjectOut,
    ProviderIn,
    ProviderOut,
    UseCaseIn,
    UseCaseOut,
    VirtualKeyCreated,
    VirtualKeyIn,
    VirtualKeyOut,
)

router = APIRouter(prefix="/api")


def require_admin(
    authorization: str = Header(default=""),
    x_openmetric_admin_token: str = Header(default=""),
) -> None:
    """No-op when no admin token is configured (the localhost case)."""
    expected = get_settings().admin_token.strip()
    if not expected:
        return
    supplied = x_openmetric_admin_token.strip()
    if not supplied and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not crypto.constant_time_equals(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token.")


admin = Depends(require_admin)


def _filters(
    days: int = Query(30, ge=1, le=3650),
    project: str | None = None,
    use_case: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    credential: str | None = None,
    only_errors: bool = False,
    exclude_errors: bool = False,
) -> analytics.Filters:
    return analytics.Filters(
        days=days,
        project=project,
        use_case=use_case,
        provider=provider,
        model=model,
        credential=credential,
        only_errors=only_errors,
        exclude_errors=exclude_errors,
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "events": db.scalar(select(func.count(RequestEvent.id))) or 0,
        "projects": db.scalar(select(func.count(Project.id))) or 0,
        "providers": db.scalar(select(func.count(Provider.id))) or 0,
        "credentials": db.scalar(select(func.count(Credential.id))) or 0,
        "encryption_configured": bool(settings.secret_key),
        "admin_token_set": bool(settings.admin_token),
        "body_logging_enabled": settings.bodies_are_logged,
    }


# --------------------------------------------------------------------------- #
# Projects / use cases
# --------------------------------------------------------------------------- #


@router.get("/projects", response_model=list[ProjectOut], dependencies=[admin])
def list_projects(include_archived: bool = False, db: Session = Depends(get_db)):
    stmt = select(Project).order_by(Project.slug)
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    return list(db.scalars(stmt))


@router.post("/projects", response_model=ProjectOut, status_code=201, dependencies=[admin])
def create_project(payload: ProjectIn, db: Session = Depends(get_db)):
    slug = payload.slug.strip().lower()
    if db.scalar(select(Project).where(Project.slug == slug)):
        raise HTTPException(409, f"Project '{slug}' already exists.")
    project = Project(
        slug=slug,
        name=payload.name or slug.replace("-", " ").title(),
        description=payload.description,
        monthly_budget_usd=payload.monthly_budget_usd,
    )
    db.add(project)
    db.flush()
    return project


@router.patch("/projects/{slug}", response_model=ProjectOut, dependencies=[admin])
def update_project(slug: str, payload: ProjectIn, db: Session = Depends(get_db)):
    project = db.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise HTTPException(404, f"No project '{slug}'.")
    project.name = payload.name or project.name
    project.description = payload.description or project.description
    project.monthly_budget_usd = payload.monthly_budget_usd
    return project


@router.delete("/projects/{slug}", status_code=204, dependencies=[admin])
def archive_project(slug: str, db: Session = Depends(get_db)):
    project = db.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise HTTPException(404, f"No project '{slug}'.")
    project.archived = True
    return Response(status_code=204)


@router.get("/use-cases", response_model=list[UseCaseOut], dependencies=[admin])
def list_use_cases(db: Session = Depends(get_db)):
    return list(db.scalars(select(UseCase).order_by(UseCase.slug)))


@router.post("/use-cases", response_model=UseCaseOut, status_code=201, dependencies=[admin])
def create_use_case(payload: UseCaseIn, db: Session = Depends(get_db)):
    slug = payload.slug.strip().lower()
    if db.scalar(select(UseCase).where(UseCase.slug == slug)):
        raise HTTPException(409, f"Use case '{slug}' already exists.")
    use_case = UseCase(
        slug=slug,
        name=payload.name or slug.replace("-", " ").title(),
        description=payload.description,
    )
    db.add(use_case)
    db.flush()
    return use_case


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


@router.get("/providers", response_model=list[ProviderOut], dependencies=[admin])
def list_providers(db: Session = Depends(get_db)):
    counts = dict(
        db.execute(
            select(Credential.provider_id, func.count(Credential.id)).group_by(
                Credential.provider_id
            )
        ).all()
    )
    return [
        ProviderOut(
            id=p.id,
            slug=p.slug,
            name=p.name,
            base_url=p.base_url,
            kind=p.kind,
            auth_style=p.auth_style,
            env_var=p.env_var,
            credential_count=counts.get(p.id, 0),
        )
        for p in db.scalars(select(Provider).order_by(Provider.slug))
    ]


@router.post("/providers", response_model=ProviderOut, status_code=201, dependencies=[admin])
def create_provider(payload: ProviderIn, db: Session = Depends(get_db)):
    slug = payload.slug.strip().lower()
    if db.scalar(select(Provider).where(Provider.slug == slug)):
        raise HTTPException(409, f"Provider '{slug}' already exists.")
    provider = Provider(
        slug=slug,
        name=payload.name or slug.title(),
        base_url=payload.base_url.rstrip("/"),
        kind=payload.kind,
        auth_style=payload.auth_style,
        auth_header=payload.auth_header,
        auth_query_param=payload.auth_query_param,
        env_var=payload.env_var,
    )
    db.add(provider)
    db.flush()
    return ProviderOut(
        id=provider.id,
        slug=provider.slug,
        name=provider.name,
        base_url=provider.base_url,
        kind=provider.kind,
        auth_style=provider.auth_style,
        env_var=provider.env_var,
    )


@router.get("/catalog", dependencies=[admin])
def catalog() -> dict[str, Any]:
    """Built-in providers and known model rates, for the 'add provider' UI."""
    data = pricing.load_catalog()
    return {
        "version": data.get("version"),
        "updated": data.get("updated"),
        "providers": data.get("providers", {}),
        "priced_models": sorted(data.get("token_prices", {}).keys()),
        "local_overrides_file": str(pricing.local_pricing_path()),
    }


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


def _credential_out(credential: Credential) -> CredentialOut:
    return CredentialOut(
        id=credential.id,
        provider=credential.provider.slug if credential.provider else "?",
        project=credential.project.slug if credential.project else None,
        label=credential.label,
        key_hint=credential.key_hint,
        key_fingerprint=credential.key_fingerprint,
        active=credential.active,
        notes=credential.notes,
        created_at=credential.created_at,
        last_used_at=credential.last_used_at,
    )


@router.get("/credentials", response_model=list[CredentialOut], dependencies=[admin])
def list_credentials(db: Session = Depends(get_db)):
    return [_credential_out(c) for c in db.scalars(select(Credential).order_by(Credential.label))]


@router.post("/credentials", response_model=CredentialOut, status_code=201, dependencies=[admin])
def create_credential(payload: CredentialIn, db: Session = Depends(get_db)):
    provider = db.scalar(select(Provider).where(Provider.slug == payload.provider.lower()))
    if provider is None:
        raise HTTPException(404, f"No provider '{payload.provider}'. Add it first.")
    project = None
    if payload.project:
        project = db.scalar(select(Project).where(Project.slug == payload.project.lower()))
        if project is None:
            raise HTTPException(404, f"No project '{payload.project}'.")
    if db.scalar(
        select(Credential).where(
            Credential.provider_id == provider.id, Credential.label == payload.label
        )
    ):
        raise HTTPException(409, f"Credential '{payload.label}' already exists for this provider.")

    secret = payload.api_key.strip()
    credential = Credential(
        provider_id=provider.id,
        project_id=project.id if project else None,
        label=payload.label,
        encrypted_secret=crypto.encrypt(secret),
        key_hint=crypto.hint(secret),
        key_fingerprint=crypto.fingerprint(secret),
        notes=payload.notes,
    )
    db.add(credential)
    db.flush()
    db.refresh(credential)
    return _credential_out(credential)


@router.post("/credentials/{label}/deactivate", response_model=CredentialOut, dependencies=[admin])
def deactivate_credential(label: str, db: Session = Depends(get_db)):
    credential = db.scalar(select(Credential).where(Credential.label == label))
    if credential is None:
        raise HTTPException(404, f"No credential '{label}'.")
    credential.active = False
    return _credential_out(credential)


@router.delete("/credentials/{label}", status_code=204, dependencies=[admin])
def delete_credential(label: str, db: Session = Depends(get_db)):
    credential = db.scalar(select(Credential).where(Credential.label == label))
    if credential is None:
        raise HTTPException(404, f"No credential '{label}'.")
    db.delete(credential)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Virtual keys
# --------------------------------------------------------------------------- #


def _vkey_out(key: VirtualKey) -> dict[str, Any]:
    return {
        "id": key.id,
        "name": key.name,
        "token_hint": key.token_hint,
        "project": key.project.slug if key.project else None,
        "use_case": key.use_case.slug if key.use_case else None,
        "credential": key.credential.label if key.credential else None,
        "active": key.active,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
    }


@router.get("/virtual-keys", response_model=list[VirtualKeyOut], dependencies=[admin])
def list_virtual_keys(db: Session = Depends(get_db)):
    return [_vkey_out(k) for k in db.scalars(select(VirtualKey).order_by(VirtualKey.name))]


@router.post(
    "/virtual-keys", response_model=VirtualKeyCreated, status_code=201, dependencies=[admin]
)
def create_virtual_key(payload: VirtualKeyIn, db: Session = Depends(get_db)):
    project = use_case = credential = None
    if payload.project:
        project = db.scalar(select(Project).where(Project.slug == payload.project.lower()))
        if project is None:
            raise HTTPException(404, f"No project '{payload.project}'.")
    if payload.use_case:
        use_case = db.scalar(select(UseCase).where(UseCase.slug == payload.use_case.lower()))
        if use_case is None:
            raise HTTPException(404, f"No use case '{payload.use_case}'.")
    if payload.credential_label:
        credential = db.scalar(
            select(Credential).where(Credential.label == payload.credential_label)
        )
        if credential is None:
            raise HTTPException(404, f"No credential '{payload.credential_label}'.")

    token = crypto.new_virtual_key()
    key = VirtualKey(
        name=payload.name,
        token_hash=crypto.hash_virtual_key(token),
        token_hint=f"...{token[-6:]}",
        project_id=project.id if project else None,
        use_case_id=use_case.id if use_case else None,
        credential_id=credential.id if credential else None,
    )
    db.add(key)
    db.flush()
    db.refresh(key)
    return VirtualKeyCreated(token=token, **_vkey_out(key))


@router.delete("/virtual-keys/{key_id}", status_code=204, dependencies=[admin])
def revoke_virtual_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(VirtualKey, key_id)
    if key is None:
        raise HTTPException(404, "No such virtual key.")
    key.active = False
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #


@router.get("/setup", dependencies=[admin])
def setup_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Where a new install is in its first-run journey. Drives the Setup tab."""
    settings = get_settings()
    events = db.scalar(select(func.count(RequestEvent.id))) or 0
    credentials = (
        db.scalar(select(func.count(Credential.id)).where(Credential.active.is_(True))) or 0
    )
    virtual_keys = (
        db.scalar(select(func.count(VirtualKey.id)).where(VirtualKey.active.is_(True))) or 0
    )
    projects = db.scalar(select(func.count(Project.id)).where(Project.archived.is_(False))) or 0
    first_event = db.scalar(select(func.min(RequestEvent.created_at)))
    last_event = db.scalar(select(func.max(RequestEvent.created_at)))
    unpriced_providers = [
        row[0]
        for row in db.execute(
            select(Provider.slug)
            .join(RequestEvent, RequestEvent.provider_id == Provider.id)
            .where(RequestEvent.cost_source == "unknown", RequestEvent.model == "")
            .group_by(Provider.slug)
        ).all()
    ]
    steps = [
        {
            "id": "encryption",
            "title": "Encryption key configured",
            "done": bool(settings.secret_key),
            "hint": "Run `openmetric init` to generate one into .env.",
        },
        {
            "id": "credential",
            "title": "First provider key stored",
            "done": credentials > 0,
            "hint": "Add one below, or `openmetric key add openrouter --label personal`.",
        },
        {
            "id": "virtual_key",
            "title": "First virtual key issued",
            "done": virtual_keys > 0,
            "hint": "Issue one per project below. Your app uses it instead of a provider key.",
        },
        {
            "id": "first_request",
            "title": "First request measured",
            "done": events > 0,
            "hint": "Point an SDK at this gateway and make one call.",
        },
        {
            "id": "pricing",
            "title": "Every provider priced",
            "done": events > 0 and not unpriced_providers,
            "hint": (
                "Set a rate for: " + ", ".join(unpriced_providers)
                if unpriced_providers
                else "Set per-request rates for scraping and search providers."
            ),
        },
    ]
    return {
        "complete": all(step["done"] for step in steps),
        "steps": steps,
        "counts": {
            "events": events,
            "credentials": credentials,
            "virtual_keys": virtual_keys,
            "projects": projects,
        },
        "first_event_at": first_event.isoformat() if first_event else None,
        "last_event_at": last_event.isoformat() if last_event else None,
        "unpriced_providers": unpriced_providers,
        "gateway": {"host": settings.host, "port": settings.port},
        "admin_token_set": bool(settings.admin_token),
    }


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #


@router.get("/analytics/summary", dependencies=[admin])
def analytics_summary(
    compare: bool = Query(False, description="Include the previous period and % deltas."),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    if compare:
        return analytics.summary_with_comparison(db, filters)
    return analytics.summary(db, filters)


@router.get("/analytics/group", dependencies=[admin])
def analytics_group(
    by: str = Query("project"),
    limit: int = Query(50, ge=1, le=500),
    order_by: str = Query("cost_usd"),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    try:
        return {"dimension": by, "rows": analytics.group(db, filters, by, limit, order_by)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/analytics/timeseries", dependencies=[admin])
def analytics_timeseries(
    bucket: str = Query("day", pattern="^(hour|day|month)$"),
    by: str | None = None,
    limit: int = Query(8, ge=1, le=20),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    try:
        return analytics.timeseries(db, filters, bucket, by, limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/analytics/matrix", dependencies=[admin])
def analytics_matrix(
    rows_by: str = Query("project"),
    cols_by: str = Query("provider"),
    metric: str = Query("cost_usd"),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    try:
        return analytics.matrix(db, filters, rows_by, cols_by, metric)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/analytics/events", dependencies=[admin])
def analytics_events(
    limit: int = Query(100, ge=1, le=1000),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    return {"events": analytics.recent_events(db, filters, limit)}


@router.get("/analytics/budgets", dependencies=[admin])
def analytics_budgets(db: Session = Depends(get_db)):
    return {"budgets": analytics.budget_status(db, analytics.Filters())}


@router.get("/analytics/dimensions", dependencies=[admin])
def analytics_dimensions():
    return {"dimensions": sorted(analytics.DIMENSIONS.keys())}


@router.get("/blindspots", dependencies=[admin])
def api_blindspots(
    idle_days: int = Query(30, ge=1, le=365),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    return blindspots.run_all(db, filters, idle_days)


@router.get("/export.csv", dependencies=[admin])
def export_csv(
    limit: int = Query(10000, ge=1, le=200_000),
    filters: analytics.Filters = Depends(_filters),
    db: Session = Depends(get_db),
):
    """Raw events as CSV. Contains no keys and no prompt text."""
    rows = analytics.recent_events(db, filters, limit)
    buffer = io.StringIO()
    columns = [
        "created_at",
        "project",
        "use_case",
        "provider",
        "credential",
        "model",
        "operation",
        "status_code",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "units",
        "cost_usd",
        "cost_source",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="openmetric-{stamp}.csv"'},
    )
