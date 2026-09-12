"""Database schema.

The shape mirrors how people actually work:

    Project        "the thing I am building"        (side-project-a, client-dashboard)
      |
    UseCase        "what the call is for"           (llm-summary, scraping, embeddings)
      |
    Provider       "who I buy it from"              (openrouter, openai, firecrawl)
      |
    Credential     "which key paid for it"          (one of several OpenRouter keys)
      |
    RequestEvent   one proxied call, with cost

A VirtualKey is what your application holds. It carries the project + use-case
tagging, so your code never sees a real provider key again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    monthly_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    credentials: Mapped[list[Credential]] = relationship(back_populates="project")


class UseCase(Base):
    __tablename__ = "use_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    auth_style: Mapped[str] = mapped_column(String(32), default="bearer")
    auth_header: Mapped[str] = mapped_column(String(64), default="Authorization")
    auth_query_param: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="llm")  # llm | scrape | search | other
    env_var: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    credentials: Mapped[list[Credential]] = relationship(back_populates="provider")


class Credential(Base):
    """A provider API key. The plaintext exists only inside ``encrypted_secret``."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("provider_id", "label", name="uq_credential_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    encrypted_secret: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(32))
    key_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    provider: Mapped[Provider] = relationship(back_populates="credentials")
    project: Mapped[Project | None] = relationship(back_populates="credentials")


class VirtualKey(Base):
    """The token your application sends to OpenMetric instead of a provider key."""

    __tablename__ = "virtual_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(32))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    use_case_id: Mapped[int | None] = mapped_column(ForeignKey("use_cases.id"), nullable=True)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project | None] = relationship()
    use_case: Mapped[UseCase | None] = relationship()
    credential: Mapped[Credential | None] = relationship()


class RequestEvent(Base):
    """One proxied call. No prompt or response text unless you opt in."""

    __tablename__ = "request_events"
    __table_args__ = (
        Index("ix_events_created_project", "created_at", "project_id"),
        Index("ix_events_created_provider", "created_at", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    use_case_id: Mapped[int | None] = mapped_column(ForeignKey("use_cases.id"), nullable=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"), nullable=True)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    virtual_key_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_keys.id"), nullable=True)

    model: Mapped[str] = mapped_column(String(200), default="")
    operation: Mapped[str] = mapped_column(String(64), default="")
    method: Mapped[str] = mapped_column(String(10), default="POST")
    path: Mapped[str] = mapped_column(String(500), default="")
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_type: Mapped[str] = mapped_column(String(64), default="")

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    streamed: Mapped[bool] = mapped_column(Boolean, default=False)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[float] = mapped_column(Float, default=0.0)  # non-LLM billing units
    unit_kind: Mapped[str] = mapped_column(String(32), default="")

    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_source: Mapped[str] = mapped_column(
        String(16), default="unknown"
    )  # upstream|catalog|unknown

    request_bytes: Mapped[int] = mapped_column(Integer, default=0)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0)

    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Only populated when body logging is explicitly enabled. Off by default.
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project | None] = relationship()
    use_case: Mapped[UseCase | None] = relationship()
    provider: Mapped[Provider | None] = relationship()
    credential: Mapped[Credential | None] = relationship()
