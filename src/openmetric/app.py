"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import __version__, api, gateway, pricing
from .config import get_settings
from .db import init_db, session_scope
from .models import Provider
from .redaction import safe_error

log = logging.getLogger("openmetric")
STATIC_DIR = Path(__file__).parent / "static"


def sync_builtin_providers() -> int:
    """Make the catalog's providers available in the database. Idempotent."""
    added = 0
    with session_scope() as session:
        for slug, spec in pricing.known_providers().items():
            if session.scalar(select(Provider).where(Provider.slug == slug)):
                continue
            session.add(
                Provider(
                    slug=slug,
                    name=spec.get("name", slug.title()),
                    base_url=str(spec.get("base_url", "")).rstrip("/"),
                    kind=spec.get("kind", "other"),
                    auth_style=spec.get("auth_style", "bearer"),
                    auth_header=spec.get("auth_header", "Authorization"),
                    auth_query_param=spec.get("auth_query_param"),
                    env_var=spec.get("env_var", ""),
                )
            )
            added += 1
    return added


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    added = sync_builtin_providers()
    if added:
        log.info("registered %d built-in providers", added)
    settings = get_settings()
    if settings.bodies_are_logged:
        log.warning(
            "Body logging is ON: prompt/response text will be stored in your database. "
            "Set OPENMETRIC_LOG_REQUEST_BODIES=false and OPENMETRIC_LOG_RESPONSE_BODIES=false "
            "to turn it off."
        )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenMetric",
        version=__version__,
        description=(
            "One gateway for every API key you use, with per-project cost visibility. "
            "Self-hosted; your keys and usage never leave your machine."
        ),
        lifespan=lifespan,
    )

    # Local dashboards and local dev servers only. Widen deliberately if you must.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def _redacted_errors(_request, exc: Exception):
        """Never let an exception string carry a credential into a response."""
        log.error("unhandled error: %s", safe_error(exc))
        return JSONResponse(
            status_code=500,
            content={"error": {"message": safe_error(exc), "type": "openmetric_internal_error"}},
        )

    app.include_router(api.router)
    app.include_router(gateway.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def dashboard():
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
