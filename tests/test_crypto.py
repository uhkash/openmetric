from openmetric import crypto


def test_encrypt_decrypt_roundtrip():
    secret = "sk-or-v1-abcdefghijklmnopqrstuvwxyz"
    ciphertext = crypto.encrypt(secret)
    assert secret not in ciphertext
    assert crypto.decrypt(ciphertext) == secret


def test_ciphertext_differs_per_call():
    """Fernet includes a nonce, so identical keys do not produce identical blobs."""
    secret = "sk-test-000000000000000000"
    assert crypto.encrypt(secret) != crypto.encrypt(secret)


def test_fingerprint_is_stable_and_not_reversible():
    secret = "sk-test-000000000000000000"
    assert crypto.fingerprint(secret) == crypto.fingerprint(secret)
    assert crypto.fingerprint(secret) != crypto.fingerprint(secret + "x")
    assert secret[4:] not in crypto.fingerprint(secret)


def test_hint_reveals_only_the_edges():
    secret = "sk-or-v1-abcdefghijklmnop9f2a"
    assert crypto.hint(secret).endswith("9f2a")
    assert "ghijklmnop" not in crypto.hint(secret)


def test_virtual_key_is_hashed_not_stored():
    token = crypto.new_virtual_key()
    assert token.startswith("om_live_")
    digest = crypto.hash_virtual_key(token)
    assert token not in digest
    assert crypto.hash_virtual_key(token) == digest


def test_missing_secret_key_raises(monkeypatch):
    import pytest

    from openmetric.config import reset_settings_cache

    monkeypatch.setenv("OPENMETRIC_SECRET_KEY", "")
    reset_settings_cache()
    with pytest.raises(crypto.MissingSecretKey):
        crypto.encrypt("anything")


def test_wrong_key_cannot_decrypt(monkeypatch):
    import pytest
    from cryptography.fernet import Fernet

    from openmetric.config import reset_settings_cache

    ciphertext = crypto.encrypt("sk-secret-value-000000")
    monkeypatch.setenv("OPENMETRIC_SECRET_KEY", Fernet.generate_key().decode())
    reset_settings_cache()
    with pytest.raises(crypto.MissingSecretKey):
        crypto.decrypt(ciphertext)
