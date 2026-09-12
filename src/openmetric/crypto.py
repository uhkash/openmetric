"""Encryption of provider credentials at rest.

Design rules that keep this repo safe to publish:

* The plaintext of a provider key is written to exactly one place - an encrypted
  blob in the local database - and is decrypted only to sign an upstream request.
* Everything user-facing (API responses, dashboard, CLI, logs) sees only the
  last four characters and a non-reversible fingerprint.
* The encryption key itself lives in the environment, never in the database and
  never in the repository.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

VIRTUAL_KEY_PREFIX = "om"


class MissingSecretKey(RuntimeError):
    """Raised when OPENMETRIC_SECRET_KEY is absent or malformed."""


def generate_secret_key() -> str:
    """A fresh Fernet key, suitable for OPENMETRIC_SECRET_KEY."""
    return Fernet.generate_key().decode()


def _cipher() -> Fernet:
    key = get_settings().secret_key.strip()
    if not key:
        raise MissingSecretKey(
            "OPENMETRIC_SECRET_KEY is not set. Run `openmetric init` to create one, "
            "then keep it out of version control."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:  # malformed key material
        raise MissingSecretKey(
            "OPENMETRIC_SECRET_KEY is not a valid Fernet key. Generate one with `openmetric init`."
        ) from exc


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise MissingSecretKey(
            "Stored credential could not be decrypted. This usually means "
            "OPENMETRIC_SECRET_KEY changed since the credential was added."
        ) from exc


def fingerprint(plaintext: str) -> str:
    """Stable, non-reversible id for a secret.

    Lets you answer "is this the same key I added last month?" and spot the same
    key reused across projects, without ever storing the key itself.
    """
    return hashlib.sha256(plaintext.encode()).hexdigest()[:16]


def last4(plaintext: str) -> str:
    return plaintext[-4:] if len(plaintext) >= 4 else "?" * len(plaintext)


def hint(plaintext: str) -> str:
    """What humans see in the UI, e.g. ``sk-or...9f2a``."""
    head = plaintext[:5] if len(plaintext) > 12 else ""
    return f"{head}...{last4(plaintext)}" if head else f"...{last4(plaintext)}"


def new_virtual_key(env: str = "live") -> str:
    """Client-facing token. This is what your app sends to OpenMetric."""
    return f"{VIRTUAL_KEY_PREFIX}_{env}_{secrets.token_urlsafe(24)}"


def hash_virtual_key(token: str) -> str:
    """Virtual keys are stored hashed, so a leaked database is not a leaked gateway."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
