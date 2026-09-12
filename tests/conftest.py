"""Test fixtures. Every test runs against a throwaway database and key."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """A fresh encryption key, database and pricing file per test."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("OPENMETRIC_ENV_FILE", str(tmp_path / "nonexistent.env"))
    monkeypatch.setenv("OPENMETRIC_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OPENMETRIC_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("OPENMETRIC_PRICING_FILE", str(tmp_path / "pricing.local.yaml"))
    monkeypatch.setenv("OPENMETRIC_ADMIN_TOKEN", "")
    monkeypatch.setenv("OPENMETRIC_LOG_REQUEST_BODIES", "false")
    monkeypatch.setenv("OPENMETRIC_LOG_RESPONSE_BODIES", "false")
    monkeypatch.chdir(tmp_path)

    from openmetric import pricing
    from openmetric.config import reset_settings_cache
    from openmetric.db import reset_engine

    reset_settings_cache()
    reset_engine()
    pricing.reload_catalog()
    yield
    reset_engine()
    reset_settings_cache()
    pricing.reload_catalog()


@pytest.fixture
def session():
    from openmetric.db import init_db, session_scope

    init_db()
    with session_scope() as db:
        yield db


@pytest.fixture
def client():
    """TestClient with the app's startup hooks run."""
    from fastapi.testclient import TestClient

    from openmetric.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def configured(client):
    """A project, a provider credential and a virtual key, ready to proxy with."""
    from openmetric import crypto
    from openmetric.db import session_scope
    from openmetric.models import Credential, Project, Provider, UseCase, VirtualKey

    secret = "sk-or-v1-THIS-IS-A-TEST-KEY-000000000000"
    with session_scope() as db:
        project = Project(slug="demo-app", name="Demo App")
        use_case = UseCase(slug="llm-summary", name="LLM Summary")
        db.add_all([project, use_case])
        db.flush()

        provider = db.query(Provider).filter_by(slug="openrouter").one()
        credential = Credential(
            provider_id=provider.id,
            project_id=project.id,
            label="openrouter-test",
            encrypted_secret=crypto.encrypt(secret),
            key_hint=crypto.hint(secret),
            key_fingerprint=crypto.fingerprint(secret),
        )
        db.add(credential)
        db.flush()

        token = crypto.new_virtual_key("test")
        db.add(
            VirtualKey(
                name="demo-key",
                token_hash=crypto.hash_virtual_key(token),
                token_hint=f"...{token[-6:]}",
                project_id=project.id,
                use_case_id=use_case.id,
                credential_id=credential.id,
            )
        )
    return {"token": token, "provider_secret": secret, "client": client}
