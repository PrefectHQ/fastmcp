"""
Shared UI utilities for FastMCP HTML pages.

This module provides reusable HTML/CSS components for OAuth callbacks,
consent pages, and other user-facing interfaces.
"""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse

# FastMCP branding
FASTMCP_LOGO_URL = "https://gofastmcp.com/assets/brand/blue-logo.png"

# Base CSS styles shared across all FastMCP pages
BASE_STYLES = """
    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }

    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        margin: 0;
        padding: 0;
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #f9fafb;
        color: #0a0a0a;
    }

    .container {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        padding: 3rem 2.5rem;
        border-radius: 1rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        text-align: center;
        max-width: 36rem;
        margin: 1rem;
        width: 100%;
    }

    @media (max-width: 640px) {
        .container {
            padding: 2rem 1.5rem;
            margin: 0.5rem;
        }
    }

    .logo {
        width: 64px;
        height: auto;
        margin-bottom: 1.5rem;
        display: block;
        margin-left: auto;
        margin-right: auto;
    }

    h1 {
        font-size: 1.5rem;
        font-weight: 600;
        margin-bottom: 1.5rem;
        color: #111827;
    }
"""

# Button styles
BUTTON_STYLES = """
    .button-group {
        display: flex;
        gap: 0.75rem;
        margin-top: 1.5rem;
        justify-content: center;
    }

    button {
        padding: 0.75rem 2rem;
        font-size: 0.9375rem;
        font-weight: 500;
        border-radius: 0.5rem;
        border: none;
        cursor: pointer;
        transition: all 0.15s;
        font-family: inherit;
    }

    button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }

    .btn-approve, .btn-primary {
        background: #10b981;
        color: #ffffff;
        min-width: 120px;
    }

    .btn-deny, .btn-secondary {
        background: #6b7280;
        color: #ffffff;
        min-width: 120px;
    }
"""

# Info box / message box styles
INFO_BOX_STYLES = """
    .info-box {
        background: #f0f9ff;
        border: 1px solid #bae6fd;
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 1.5rem;
        text-align: left;
        font-size: 0.9375rem;
        line-height: 1.5;
        color: #374151;
    }

    .info-box p {
        margin-bottom: 0.5rem;
    }

    .info-box p:last-child {
        margin-bottom: 0;
    }

    .info-box.centered {
        text-align: center;
    }

    .info-box.error {
        background: #fef2f2;
        border-color: #fecaca;
        color: #991b1b;
    }

    .info-box strong {
        color: #0ea5e9;
        font-weight: 600;
    }

    .info-box .server-name-link {
        color: #0ea5e9;
        text-decoration: underline;
        font-weight: 600;
        cursor: pointer;
        transition: opacity 0.15s;
    }

    .info-box .server-name-link:hover {
        opacity: 0.8;
    }

    /* Monospace info box - gray styling with code font */
    .info-box-mono {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 0.5rem;
        padding: 0.875rem;
        margin: 1.25rem 0;
        font-size: 0.875rem;
        color: #6b7280;
        font-family: 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
        text-align: left;
    }

    .info-box-mono.centered {
        text-align: center;
    }

    .info-box-mono.error {
        background: #fef2f2;
        border-color: #fecaca;
        color: #991b1b;
    }

    .info-box-mono strong {
        color: #111827;
        font-weight: 600;
    }

    .warning-box {
        background: #f0f9ff;
        border: 1px solid #bae6fd;
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 1.5rem;
        text-align: center;
    }

    .warning-box p {
        margin-bottom: 0.5rem;
        line-height: 1.5;
        color: #6b7280;
        font-size: 0.9375rem;
    }

    .warning-box p:last-child {
        margin-bottom: 0;
    }

    .warning-box strong {
        color: #0ea5e9;
        font-weight: 600;
    }

    .warning-box a {
        color: #0ea5e9;
        text-decoration: underline;
        font-weight: 600;
    }

    .warning-box a:hover {
        color: #0284c7;
        text-decoration: underline;
    }
"""

# Status message styles (for success/error indicators)
STATUS_MESSAGE_STYLES = """
    .status-message {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.75rem;
        margin-bottom: 1.5rem;
    }

    .status-icon {
        font-size: 1.5rem;
        line-height: 1;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 2rem;
        height: 2rem;
        border-radius: 0.5rem;
        flex-shrink: 0;
    }

    .status-icon.success {
        background: #10b98120;
    }

    .status-icon.error {
        background: #ef444420;
    }

    .message {
        font-size: 1.125rem;
        line-height: 1.75;
        color: #111827;
        font-weight: 600;
        text-align: left;
    }
"""

# Detail box styles (for key-value pairs)
DETAIL_BOX_STYLES = """
    .detail-box {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 1.5rem;
        text-align: left;
    }

    .detail-row {
        display: flex;
        padding: 0.5rem 0;
        border-bottom: 1px solid #e5e7eb;
    }

    .detail-row:last-child {
        border-bottom: none;
    }

    .detail-label {
        font-weight: 600;
        min-width: 160px;
        color: #6b7280;
        font-size: 0.875rem;
        flex-shrink: 0;
        padding-right: 1rem;
    }

    .detail-value {
        flex: 1;
        font-family: 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
        font-size: 0.75rem;
        color: #111827;
        word-break: break-all;
        overflow-wrap: break-word;
    }
"""

# Redirect section styles (for OAuth redirect URI box)
REDIRECT_SECTION_STYLES = """
    .redirect-section {
        background: #fffbeb;
        border: 1px solid #fcd34d;
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 1.5rem;
        text-align: left;
    }

    .redirect-section .label {
        font-size: 0.875rem;
        color: #6b7280;
        font-weight: 600;
        margin-bottom: 0.5rem;
        display: block;
    }

    .redirect-section .value {
        font-family: 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
        font-size: 0.875rem;
        color: #111827;
        word-break: break-all;
        margin-top: 0.25rem;
    }
"""

# Collapsible details styles
DETAILS_STYLES = """
    details {
        margin-bottom: 1.5rem;
        text-align: left;
    }

    summary {
        cursor: pointer;
        font-size: 0.875rem;
        color: #6b7280;
        font-weight: 600;
        list-style: none;
        padding: 0.5rem;
        border-radius: 0.25rem;
    }

    summary:hover {
        background: #f9fafb;
    }

    summary::marker {
        display: none;
    }

    summary::before {
        content: "▶";
        display: inline-block;
        margin-right: 0.5rem;
        transition: transform 0.2s;
        font-size: 0.75rem;
    }

    details[open] summary::before {
        transform: rotate(90deg);
    }
"""

# Helper text styles
HELPER_TEXT_STYLES = """
    .close-instruction, .help-text {
        font-size: 0.875rem;
        color: #6b7280;
        margin-top: 1.5rem;
    }
"""

# Tooltip styles for hover help
TOOLTIP_STYLES = """
    .help-link-container {
        position: fixed;
        bottom: 1.5rem;
        right: 1.5rem;
        font-size: 0.875rem;
    }

    .help-link {
        color: #6b7280;
        text-decoration: none;
        cursor: help;
        position: relative;
        display: inline-block;
        border-bottom: 1px dotted #9ca3af;
    }

    @media (max-width: 640px) {
        .help-link {
            background: #ffffff;
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
        }
    }

    .help-link:hover {
        color: #111827;
        border-bottom-color: #111827;
    }

    .help-link:hover .tooltip {
        opacity: 1;
        visibility: visible;
    }

    .tooltip {
        position: absolute;
        bottom: 100%;
        right: 0;
        left: auto;
        margin-bottom: 0.5rem;
        background: #1f2937;
        color: #ffffff;
        padding: 0.75rem 1rem;
        border-radius: 0.5rem;
        font-size: 0.8125rem;
        line-height: 1.5;
        width: 280px;
        max-width: calc(100vw - 3rem);
        opacity: 0;
        visibility: hidden;
        transition: opacity 0.2s, visibility 0.2s;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        text-align: left;
    }

    .tooltip::after {
        content: '';
        position: absolute;
        top: 100%;
        right: 1rem;
        border: 6px solid transparent;
        border-top-color: #1f2937;
    }

    .tooltip-link {
        color: #60a5fa;
        text-decoration: underline;
    }
"""


def create_page(
    content: str,
    title: str = "FastMCP",
    additional_styles: str = "",
    csp_policy: str = "default-src 'none'; style-src 'unsafe-inline'; img-src https:; base-uri 'none'",
) -> str:
    """
    Create a complete HTML page with FastMCP styling.

    Args:
        content: HTML content to place inside the page
        title: Page title
        additional_styles: Extra CSS to include
        csp_policy: Content Security Policy header value

    Returns:
        Complete HTML page as string
    """
    title = html.escape(title)
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            {BASE_STYLES}
            {additional_styles}
        </style>
        <meta http-equiv="Content-Security-Policy" content="{csp_policy}" />
    </head>
    <body>
        {content}
    </body>
    </html>
    """


def create_logo(icon_url: str | None = None, alt_text: str = "FastMCP") -> str:
    """Create logo HTML.

    Args:
        icon_url: Optional custom icon URL. If not provided, uses the FastMCP logo.
        alt_text: Alt text for the logo image.

    Returns:
        HTML for logo image tag.
    """
    url = icon_url or FASTMCP_LOGO_URL
    alt = html.escape(alt_text)
    return f'<img src="{html.escape(url)}" alt="{alt}" class="logo" />'


def create_status_message(message: str, is_success: bool = True) -> str:
    """
    Create a status message with icon.

    Args:
        message: Status message text
        is_success: True for success (✓), False for error (✕)

    Returns:
        HTML for status message
    """
    message = html.escape(message)
    icon = "✓" if is_success else "✕"
    icon_class = "success" if is_success else "error"

    return f"""
        <div class="status-message">
            <span class="status-icon {icon_class}">{icon}</span>
            <div class="message">{message}</div>
        </div>
    """


def create_info_box(
    content: str,
    is_error: bool = False,
    centered: bool = False,
    monospace: bool = False,
) -> str:
    """
    Create an info box.

    Args:
        content: HTML content for the info box
        is_error: True for error styling, False for normal
        centered: True to center the text, False for left-aligned
        monospace: True to use gray monospace font styling instead of blue

    Returns:
        HTML for info box
    """
    content = html.escape(content)
    base_class = "info-box-mono" if monospace else "info-box"
    classes = [base_class]
    if is_error:
        classes.append("error")
    if centered:
        classes.append("centered")
    class_str = " ".join(classes)
    return f'<div class="{class_str}">{content}</div>'


def create_detail_box(rows: list[tuple[str, str]]) -> str:
    """
    Create a detail box with key-value pairs.

    Args:
        rows: List of (label, value) tuples

    Returns:
        HTML for detail box
    """
    rows_html = "\n".join(
        f"""
        <div class="detail-row">
            <div class="detail-label">{html.escape(label)}:</div>
            <div class="detail-value">{html.escape(value)}</div>
        </div>
        """
        for label, value in rows
    )

    return f'<div class="detail-box">{rows_html}</div>'


def create_button_group(buttons: list[tuple[str, str, str]]) -> str:
    """
    Create a group of buttons.

    Args:
        buttons: List of (text, value, css_class) tuples

    Returns:
        HTML for button group
    """
    buttons_html = "\n".join(
        f'<button type="submit" name="action" value="{value}" class="{css_class}">{text}</button>'
        for text, value, css_class in buttons
    )

    return f'<div class="button-group">{buttons_html}</div>'


def create_secure_html_response(html: str, status_code: int = 200) -> HTMLResponse:
    """
    Create an HTMLResponse with security headers.

    Adds X-Frame-Options: DENY to prevent clickjacking attacks per MCP security best practices.

    Args:
        html: HTML content to return
        status_code: HTTP status code

    Returns:
        HTMLResponse with security headers
    """
    return HTMLResponse(
        content=html,
        status_code=status_code,
        headers={"X-Frame-Options": "DENY"},
    )
