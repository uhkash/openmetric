"""Request/response shapes for the management API.

Note what is absent: there is no field anywhere in this module that returns a
provider key. Credentials go in; only hints and fingerprints come out.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str | None = None
    description: str = ""
    monthly_budget_usd: float | None = None


class ProjectOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    monthly_budget_usd: float | None
    archived: bool
    created_at: datetime


class UseCaseIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str | None = None
    description: str = ""


class UseCaseOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str


class ProviderIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str | None = None
    base_url: str
    kind: str = "other"
    auth_style: str = "bearer"
    auth_header: str = "Authorization"
    auth_query_param: str | None = None
    env_var: str = ""


class ProviderOut(BaseModel):
    id: int
    slug: str
    name: str
    base_url: str
    kind: str
    auth_style: str
    env_var: str
    credential_count: int = 0


class CredentialIn(BaseModel):
    provider: str
    label: str = Field(min_length=1, max_length=120)
    api_key: str = Field(min_length=4, repr=False)
    project: str | None = None
    notes: str = ""


class CredentialOut(BaseModel):
    """Deliberately has no ``api_key``. The plaintext never leaves the database."""

    id: int
    provider: str
    project: str | None
    label: str
    key_hint: str
    key_fingerprint: str
    active: bool
    notes: str
    created_at: datetime
    last_used_at: datetime | None


class VirtualKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project: str | None = None
    use_case: str | None = None
    credential_label: str | None = None


class VirtualKeyOut(BaseModel):
    id: int
    name: str
    token_hint: str
    project: str | None
    use_case: str | None
    credential: str | None
    active: bool
    created_at: datetime
    last_used_at: datetime | None


class VirtualKeyCreated(VirtualKeyOut):
    """The only response in the API that ever contains a full token."""

    token: str
    warning: str = "Copy this token now. It is stored hashed and cannot be shown again."
