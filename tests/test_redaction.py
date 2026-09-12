import pytest

from openmetric import redaction

SECRETS = [
    "sk-or-v1-1234567890abcdefghijklmnop",
    "sk-ant-api03-abcdefghijklmnopqrstuvwx",
    "sk-proj-abcdefghijklmnopqrstuvwxyz12",
    "sk-abcdefghijklmnopqrstuvwxyz1234",
    "fc-abcdefghijklmnopqrstuvwxyz12",
    "tvly-abcdefghijklmnopqrstuv",
    "gsk_abcdefghijklmnopqrstuvwxyz12",
    "AIzaSyAbcdefghijklmnopqrstuvwxyz123456",
    "om_live_abcdefghijklmnopqrstuvwx",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_known_key_shapes_are_scrubbed(secret):
    text = f"call failed with key {secret} at 12:00"
    assert secret not in redaction.redact_text(text)
    assert redaction.REDACTED in redaction.redact_text(text)


def test_sensitive_headers_are_masked():
    headers = redaction.redact_headers(
        {"Authorization": "Bearer sk-abcdefghijklmnopqrstuvwxyz", "X-Request-Id": "abc123"}
    )
    assert headers["Authorization"] == redaction.REDACTED
    assert headers["X-Request-Id"] == "abc123"


def test_nested_objects_are_scrubbed():
    payload = {
        "config": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz", "model": "gpt-4o"},
        "items": [{"token": "secret-value"}],
    }
    cleaned = redaction.redact_obj(payload)
    assert cleaned["config"]["api_key"] == redaction.REDACTED
    assert cleaned["config"]["model"] == "gpt-4o"
    assert cleaned["items"][0]["token"] == redaction.REDACTED


def test_exception_text_is_scrubbed():
    error = ValueError("auth failed for sk-or-v1-1234567890abcdefghijklmnop")
    assert "sk-or-v1" not in redaction.safe_error(error)


def test_bearer_prefix_is_preserved_for_readability():
    assert redaction.redact_text("Bearer abcdefghijklmnopqrst").startswith("Bearer ")
