"""Guardrails for publishing this repo and running it in the open.

These tests exist to make one class of mistake loud: a provider key escaping
into a response, a log line, a database file, or a git commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import respx
from sqlalchemy import select

from openmetric.config import reset_settings_cache
from openmetric.db import session_scope
from openmetric.models import RequestEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-or-v1-SUPERSECRETVALUE-000000000000"

READ_ENDPOINTS = [
    "/api/health",
    "/api/projects",
    "/api/use-cases",
    "/api/providers",
    "/api/credentials",
    "/api/virtual-keys",
    "/api/catalog",
    "/api/analytics/summary",
    "/api/analytics/group?by=credential",
    "/api/analytics/timeseries",
    "/api/analytics/matrix",
    "/api/analytics/events",
    "/api/analytics/budgets",
    "/api/blindspots",
    "/api/export.csv",
]


def _store_credential(client):
    client.post("/api/projects", json={"slug": "leak-test"})
    response = client.post(
        "/api/credentials",
        json={
            "provider": "openrouter",
            "label": "leak-test-key",
            "api_key": SECRET,
            "project": "leak-test",
        },
    )
    assert response.status_code == 201
    return response


def test_create_credential_response_contains_no_key(client):
    body = _store_credential(client).json()
    assert SECRET not in str(body)
    assert "api_key" not in body
    assert body["key_hint"].endswith("0000")
    assert body["key_fingerprint"]


def test_no_read_endpoint_ever_returns_a_stored_key(client):
    _store_credential(client)
    for path in READ_ENDPOINTS:
        response = client.get(path)
        assert response.status_code == 200, path
        assert SECRET not in response.text, f"{path} leaked the provider key"
        assert SECRET[8:20] not in response.text, f"{path} leaked part of the provider key"


def test_stored_key_is_not_readable_in_the_database_file(client, tmp_path):
    """Someone who copies openmetric.db still does not have your keys."""
    _store_credential(client)
    db_file = tmp_path / "test.db"
    assert db_file.exists()
    blob = db_file.read_bytes()
    for wal in tmp_path.glob("test.db-*"):
        blob += wal.read_bytes()
    assert SECRET.encode() not in blob
    assert b"SUPERSECRETVALUE" not in blob


def test_virtual_key_token_is_shown_once_and_never_listed(client):
    created = client.post("/api/virtual-keys", json={"name": "one-time"}).json()
    token = created["token"]
    assert token.startswith("om_live_")

    listed = client.get("/api/virtual-keys")
    assert token not in listed.text
    assert listed.json()[0]["token_hint"].lstrip(".") == token[-6:]


def test_virtual_key_is_stored_hashed(client):
    token = client.post("/api/virtual-keys", json={"name": "hashme"}).json()["token"]
    from openmetric.models import VirtualKey

    with session_scope() as db:
        row = db.scalar(select(VirtualKey).where(VirtualKey.name == "hashme"))
        assert token not in row.token_hash
        assert len(row.token_hash) == 64


@respx.mock
def test_bodies_are_not_stored_by_default(configured):
    """Prompt and response text stay out of the database unless explicitly enabled."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"model": "gpt-4o", "choices": [{"message": {"content": "a secret answer"}}]}
        )
    )
    configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "my private prompt"}]},
    )
    with session_scope() as db:
        event = db.scalar(select(RequestEvent).order_by(RequestEvent.id.desc()))
        assert event.request_body is None
        assert event.response_body is None


@respx.mock
def test_opted_in_body_logging_is_still_redacted(configured, monkeypatch):
    monkeypatch.setenv("OPENMETRIC_LOG_REQUEST_BODIES", "true")
    reset_settings_cache()
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"model": "gpt-4o"})
    )
    configured["client"].post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['token']}"},
        json={
            "model": "gpt-4o",
            "messages": [{"content": "here is sk-abcdefghijklmnopqrstuvwxyz"}],
        },
    )
    with session_scope() as db:
        event = db.scalar(select(RequestEvent).order_by(RequestEvent.id.desc()))
        assert event.request_body is not None
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in event.request_body
        assert "[REDACTED]" in event.request_body


def test_admin_token_gates_the_management_api(client, monkeypatch):
    monkeypatch.setenv("OPENMETRIC_ADMIN_TOKEN", "correct-horse-battery-staple")
    reset_settings_cache()

    assert client.get("/api/credentials").status_code == 401
    assert (
        client.get("/api/credentials", headers={"X-OpenMetric-Admin-Token": "wrong"}).status_code
        == 401
    )
    ok = client.get(
        "/api/credentials", headers={"X-OpenMetric-Admin-Token": "correct-horse-battery-staple"}
    )
    assert ok.status_code == 200


def test_health_stays_open_for_monitoring(client, monkeypatch):
    monkeypatch.setenv("OPENMETRIC_ADMIN_TOKEN", "some-token")
    reset_settings_cache()
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Repository hygiene: these keep the published project clean.
# --------------------------------------------------------------------------- #


def test_gitignore_covers_every_secret_bearing_path():
    ignored = (REPO_ROOT / ".gitignore").read_text()
    for pattern in [".env", "*.key", "*.pem", "*.db", "*.db-wal", "*.sqlite", "secrets/"]:
        assert pattern in ignored, f"{pattern} is not gitignored"


def test_env_example_has_placeholders_only():
    example = (REPO_ROOT / ".env.example").read_text()
    assert "OPENMETRIC_SECRET_KEY=" in example
    for line in example.splitlines():
        if line.startswith("OPENMETRIC_SECRET_KEY="):
            assert line.split("=", 1)[1].strip() == "", "a real key is checked into .env.example"


def _tracked_files() -> list[str] | None:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:  # not a git checkout (e.g. installed from a wheel)
        return None
    return result.stdout.split()


def test_no_secret_bearing_file_is_tracked_by_git():
    """Catches `git add -f .env` and stray database files before they ship."""
    tracked = _tracked_files()
    if tracked is None:
        return
    forbidden_names = {".env", "pricing.local.yaml"}
    # SQLite in WAL mode writes -wal and -shm siblings that hold live database
    # contents, encrypted credentials included. They are as sensitive as the .db.
    forbidden_suffixes = (
        ".key",
        ".pem",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".db-wal",
        ".db-shm",
        ".db-journal",
    )
    for path in tracked:
        assert Path(path).name not in forbidden_names, f"{path} must not be committed"
        assert not path.endswith(forbidden_suffixes), f"{path} must not be committed"


def test_gitignore_does_not_swallow_packaged_data():
    """The price catalog lives under a `data/` directory and must still ship.

    A bare `data/` ignore pattern silently excludes it, which breaks every install
    with no error at commit time. Root-anchor the pattern instead.
    """
    tracked = _tracked_files()
    if tracked is None:
        return
    assert "src/openmetric/data/catalog.yaml" in tracked, (
        "the price catalog is not tracked by git - check the .gitignore patterns"
    )


def test_local_pricing_overrides_are_ignored_but_the_builtin_catalog_is_not():
    ignored = (REPO_ROOT / ".gitignore").read_text()
    assert "/data/" in ignored, "the local data ignore must be root-anchored"
    assert "\ndata/" not in ignored, "a bare data/ pattern would exclude packaged catalog data"


def test_catalog_contains_no_key_shaped_strings():
    catalog = (REPO_ROOT / "src/openmetric/data/catalog.yaml").read_text()
    for marker in ["sk-or-v1-", "sk-ant-", "sk-proj-", "gsk_", "AIzaSy"]:
        assert marker not in catalog
