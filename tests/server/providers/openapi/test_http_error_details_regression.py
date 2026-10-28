"""Regression tests for concise, useful OpenAPI upstream error details."""

import httpx2
import pytest

from fastmcp.server.providers.openapi.components import _raise_for_status


def _error(response: httpx2.Response) -> str:
    response.request = httpx2.Request("GET", "https://api.example.test/items")
    with pytest.raises(httpx2.HTTPStatusError) as exc_info:
        _raise_for_status(response)
    return str(exc_info.value)


@pytest.mark.parametrize(
    ("response", "expected", "unexpected"),
    [
        (
            httpx2.Response(
                301,
                headers={
                    "location": "https://api.example.test/v2/items",
                    "content-type": "text/html",
                },
                text="<html>moved</html>",
            ),
            "Location: https://api.example.test/v2/items",
            "<html>",
        ),
        (
            httpx2.Response(
                502,
                headers={"content-type": "text/html"},
                text="<!DOCTYPE html><html>" + "x" * 2000 + "</html>",
            ),
            "HTTP error 502: Bad Gateway",
            "<!DOCTYPE html>",
        ),
        (
            httpx2.Response(409, json={"detail": "conflict"}),
            '"detail": "conflict"',
            "{'detail':",
        ),
        (
            httpx2.Response(502, text="<html>proxy failure</html>"),
            "HTTP error 502: Bad Gateway",
            "proxy failure",
        ),
    ],
)
def test_error_message_preserves_details_without_html_noise(
    response: httpx2.Response, expected: str, unexpected: str
) -> None:
    message = _error(response)
    assert expected in message
    assert unexpected not in message


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        ('[1, "two"]', "application/problem+json", '[1, "two"]'),
        ('"denied"', "application/json", '"denied"'),
        ("true", "application/json", "true"),
        ("not-json", "application/json", "not-json"),
        ("", "text/plain", "HTTP error 400: Bad Request"),
    ],
)
def test_json_and_malformed_error_bodies_are_readable(
    body: str, content_type: str, expected: str
) -> None:
    response = httpx2.Response(
        400, headers={"content-type": content_type}, content=body.encode()
    )
    assert expected in _error(response)


def test_plain_text_error_body_is_bounded_with_truncation_marker() -> None:
    message = _error(httpx2.Response(500, text="x" * 1050))
    assert "x" * 1000 + "… (truncated)" in message
    assert "x" * 1001 not in message


def test_redirect_without_location_has_no_extra_detail() -> None:
    assert _error(httpx2.Response(302, text="")) == "HTTP error 302: Found"
