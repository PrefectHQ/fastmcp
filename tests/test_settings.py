import pytest
from pydantic import ValidationError

from fastmcp.settings import Settings


def test_http_host_origin_protection_defaults_to_false():
    assert Settings().http_host_origin_protection is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("auto", "auto"),
        ("true", True),
        ("false", False),
    ],
)
def test_http_host_origin_protection_env_var(value, expected, monkeypatch):
    monkeypatch.setenv("FASTMCP_HTTP_HOST_ORIGIN_PROTECTION", value)

    assert Settings().http_host_origin_protection == expected


def test_http_session_idle_timeout_defaults_to_auto():
    assert Settings().http_session_idle_timeout == "auto"


@pytest.mark.parametrize("value", ["auto", "60", "0.5"])
def test_http_session_idle_timeout_env_var(value, monkeypatch):
    monkeypatch.setenv("FASTMCP_HTTP_SESSION_IDLE_TIMEOUT", value)
    expected = "auto" if value == "auto" else float(value)
    assert Settings().http_session_idle_timeout == expected


def test_http_session_idle_timeout_can_be_disabled():
    assert Settings(http_session_idle_timeout=None).http_session_idle_timeout is None


@pytest.mark.parametrize("value", [0, -1, "invalid"])
def test_http_session_idle_timeout_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        Settings(http_session_idle_timeout=value)
