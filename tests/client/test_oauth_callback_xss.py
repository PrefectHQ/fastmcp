"""Comprehensive XSS protection tests for OAuth callback HTML rendering."""

import pytest

from fastmcp.client.oauth_callback import create_callback_html
from fastmcp.utilities.ui import (
    create_detail_box,
    create_info_box,
    create_page,
    create_status_message,
)


def test_ui_create_page_escapes_title():
    """Test that page title is properly escaped."""
    xss_title = "<script>alert(1)</script>"
    html = create_page("content", title=xss_title)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_ui_create_status_message_escapes():
    """Test that status messages are properly escaped."""
    xss_message = "<img src=x onerror=alert(1)>"
    html = create_status_message(xss_message)
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html


def test_ui_create_info_box_escapes():
    """Test that info box content is properly escaped."""
    xss_content = "<iframe src=javascript:alert(1)></iframe>"
    html = create_info_box(xss_content)
    assert "&lt;iframe" in html
    assert "<iframe src=javascript:alert(1)>" not in html


def test_ui_create_detail_box_escapes():
    """Test that detail box labels and values are properly escaped."""
    xss_label = '<script>alert("label")</script>'
    xss_value = '<script>alert("value")</script>'
    html = create_detail_box([(xss_label, xss_value)])
    assert "&lt;script&gt;" in html
    assert '<script>alert("label")</script>' not in html
    assert '<script>alert("value")</script>' not in html


def test_callback_html_escapes_error_message():
    """Test that XSS payloads in error messages are properly escaped."""
    xss_payload = "<img/src/onerror=alert(1)>"
    html = create_callback_html(xss_payload, is_success=False)

    assert "&lt;img/src/onerror=alert(1)&gt;" in html
    assert "<img/src/onerror=alert(1)>" not in html


def test_callback_html_escapes_server_url():
    """Test that XSS payloads in server_url are properly escaped."""
    xss_payload = "<script>alert(1)</script>"
    html = create_callback_html("Success", is_success=True, server_url=xss_payload)

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_callback_html_escapes_title():
    """Test that XSS payloads in title are properly escaped."""
    xss_payload = "<script>alert(document.domain)</script>"
    html = create_callback_html("Success", title=xss_payload)

    assert "&lt;script&gt;alert(document.domain)&lt;/script&gt;" in html
    assert "<script>alert(document.domain)</script>" not in html


def test_callback_html_mixed_content():
    """Test that legitimate text mixed with XSS attempts is properly escaped."""
    mixed_payload = "Error: <img src=x onerror=alert(1)> occurred"
    html = create_callback_html(mixed_payload, is_success=False)

    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Error:" in html
    assert "occurred" in html
    assert "<img src=x onerror=alert(1)>" not in html


def test_callback_html_event_handlers():
    """Test that event handler attributes are escaped."""
    xss_payload = '" onload="alert(1)'
    html = create_callback_html(xss_payload, is_success=False)

    assert "&quot; onload=&quot;alert(1)" in html
    assert '" onload="alert(1)' not in html


def test_callback_html_special_characters():
    """Test that special HTML characters are properly escaped."""
    special_chars = "&<>\"'/"
    html = create_callback_html(special_chars, is_success=False)

    assert "&amp;" in html
    assert "&lt;" in html
    assert "&gt;" in html
    assert "&quot;" in html
    # Apostrophe gets escaped to &#x27; by html.escape()
    assert "&#x27;" in html


@pytest.mark.parametrize(
    "xss_vector",
    [
        "<img src=x onerror=alert(1)>",
        "<script>alert(document.cookie)</script>",
        "<iframe src=javascript:alert(1)>",
        "<svg/onload=alert(1)>",
        "<body onload=alert(1)>",
        "<input onfocus=alert(1) autofocus>",
        "<select onfocus=alert(1) autofocus>",
        "<textarea onfocus=alert(1) autofocus>",
        "<marquee onstart=alert(1)>",
        "<div style=background:url('javascript:alert(1)')>",
    ],
)
def test_common_xss_vectors(xss_vector: str):
    """Test that common XSS attack vectors are properly escaped."""
    html = create_callback_html(xss_vector, is_success=False)

    # Should not contain the raw XSS vector
    assert xss_vector not in html

    # Should contain escaped version (at least the < and > should be escaped)
    assert "&lt;" in html
    assert "&gt;" in html


def test_legitimate_content_still_works():
    """Ensure legitimate content is displayed correctly after escaping."""
    legitimate_message = "Authentication failed: Invalid credentials"
    legitimate_url = "https://example.com:8080/mcp"

    # Error case
    html = create_callback_html(legitimate_message, is_success=False)
    assert legitimate_message in html
    assert "Authentication failed" in html

    # Success case
    html = create_callback_html("Success", is_success=True, server_url=legitimate_url)
    assert legitimate_url in html
    assert "Authentication successful" in html


def test_no_hardcoded_html_tags():
    """Verify that there are no hardcoded HTML tags that bypass escaping."""
    server_url = "test-server"
    html = create_callback_html("Success", is_success=True, server_url=server_url)

    # Should not have <strong> tags around the server URL
    assert f"<strong>{server_url}</strong>" not in html
    # Should have the server URL displayed normally (escaped)
    assert server_url in html
