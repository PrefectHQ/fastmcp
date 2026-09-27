import pytest

from fastmcp.settings import Settings


def test_http_host_origin_protection_defaults_to_auto():
    assert Settings().http_host_origin_protection == "auto"


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
