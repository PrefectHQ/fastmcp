"""OAuth Proxy UI Generation Functions.

This module contains HTML generation functions for consent and error pages.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastmcp.utilities.ui import (
    BUTTON_STYLES,
    DETAIL_BOX_STYLES,
    DETAILS_STYLES,
    INFO_BOX_STYLES,
    REDIRECT_SECTION_STYLES,
    TOOLTIP_STYLES,
    create_logo,
    create_page,
)


def create_consent_html(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    title: str = "Application Access Request",
    server_name: str | None = None,
    server_icon_url: str | None = None,
    server_website_url: str | None = None,
    client_website_url: str | None = None,
    csp_policy: str | None = None,
) -> str:
    """Create a styled HTML consent page for OAuth authorization requests.

    Args:
        csp_policy: Content Security Policy override.
            If None, uses the built-in CSP policy with appropriate directives.
            If empty string "", disables CSP entirely (no meta tag is rendered).
            If a non-empty string, uses that as the CSP policy value.
    """
    import html as html_module

    client_display = html_module.escape(client_name or client_id)
    server_name_escaped = html_module.escape(server_name or "FastMCP")

    # Make server name a hyperlink if website URL is available
    if server_website_url:
        website_url_escaped = html_module.escape(server_website_url)
        server_display = f'<a href="{website_url_escaped}" target="_blank" rel="noopener noreferrer" class="server-name-link">{server_name_escaped}</a>'
    else:
        server_display = server_name_escaped

    # Build intro box with call-to-action
    intro_box = f"""
        <div class="info-box">
            <p>The application <strong>{client_display}</strong> wants to access the MCP server <strong>{server_display}</strong>. Please ensure you recognize the callback address below.</p>
        </div>
    """

    # Build redirect URI section (yellow box, centered)
    redirect_uri_escaped = html_module.escape(redirect_uri)
    redirect_section = f"""
        <div class="redirect-section">
            <span class="label">Credentials will be sent to:</span>
            <div class="value">{redirect_uri_escaped}</div>
        </div>
    """

    # Build advanced details with collapsible section
    detail_rows = [
        ("Application Name", html_module.escape(client_name or client_id)),
        ("Application Website", html_module.escape(client_website_url or "N/A")),
        ("Application ID", client_id),
        ("Redirect URI", redirect_uri_escaped),
        (
            "Requested Scopes",
            ", ".join(html_module.escape(s) for s in scopes) if scopes else "None",
        ),
    ]

    detail_rows_html = "\n".join(
        [
            f"""
        <div class="detail-row">
            <div class="detail-label">{label}:</div>
            <div class="detail-value">{value}</div>
        </div>
        """
            for label, value in detail_rows
        ]
    )

    advanced_details = f"""
        <details>
            <summary>Advanced Details</summary>
            <div class="detail-box">
                {detail_rows_html}
            </div>
        </details>
    """

    # Build form with buttons
    # Use empty action to submit to current URL (/consent or /mcp/consent)
    # The POST handler is registered at the same path as GET
    form = f"""
        <form id="consentForm" method="POST" action="">
            <input type="hidden" name="txn_id" value="{txn_id}" />
            <input type="hidden" name="csrf_token" value="{csrf_token}" />
            <input type="hidden" name="submit" value="true" />
            <div class="button-group">
                <button type="submit" name="action" value="approve" class="btn-approve">Allow Access</button>
                <button type="submit" name="action" value="deny" class="btn-deny">Deny</button>
            </div>
        </form>
    """

    # Build help link with tooltip (identical to current implementation)
    help_link = """
        <div class="help-link-container">
            <span class="help-link">
                Why am I seeing this?
                <span class="tooltip">
                    This FastMCP server requires your consent to allow a new client
                    to connect. This protects you from <a
                    href="https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices#confused-deputy-problem"
                    target="_blank" class="tooltip-link">confused deputy
                    attacks</a>, where malicious clients could impersonate you
                    and steal access.<br><br>
                    <a
                    href="https://gofastmcp.com/servers/auth/oauth-proxy#confused-deputy-attacks"
                    target="_blank" class="tooltip-link">Learn more about
                    FastMCP security →</a>
                </span>
            </span>
        </div>
    """

    # Build the page content
    content = f"""
        <div class="container">
            {create_logo(icon_url=server_icon_url, alt_text=server_name or "FastMCP")}
            <h1>Application Access Request</h1>
            {intro_box}
            {redirect_section}
            {advanced_details}
            {form}
        </div>
        {help_link}
    """

    # Additional styles needed for this page
    additional_styles = (
        INFO_BOX_STYLES
        + REDIRECT_SECTION_STYLES
        + DETAILS_STYLES
        + DETAIL_BOX_STYLES
        + BUTTON_STYLES
        + TOOLTIP_STYLES
    )

    # Determine CSP policy to use
    # If csp_policy is None, build the default CSP policy
    # If csp_policy is empty string, CSP will be disabled entirely in create_page
    # If csp_policy is a non-empty string, use it as-is
    if csp_policy is None:
        # Need to allow form-action for form submission
        # Chrome requires explicit scheme declarations in CSP form-action when redirect chains
        # end in custom protocol schemes (e.g., cursor://). Parse redirect_uri to include its scheme.
        parsed_redirect = urlparse(redirect_uri)
        redirect_scheme = parsed_redirect.scheme.lower()

        # Build form-action directive with standard schemes plus custom protocol if present
        form_action_schemes = ["https:", "http:"]
        if redirect_scheme and redirect_scheme not in ("http", "https"):
            # Custom protocol scheme (e.g., cursor:, vscode:, etc.)
            form_action_schemes.append(f"{redirect_scheme}:")

        form_action_directive = " ".join(form_action_schemes)
        csp_policy = f"default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'; form-action {form_action_directive}"

    return create_page(
        content=content,
        title=title,
        additional_styles=additional_styles,
        csp_policy=csp_policy,
    )


def create_error_html(
    error_title: str,
    error_message: str,
    error_details: dict[str, str] | None = None,
    server_name: str | None = None,
    server_icon_url: str | None = None,
) -> str:
    """Create a styled HTML error page for OAuth errors.

    Args:
        error_title: The error title (e.g., "OAuth Error", "Authorization Failed")
        error_message: The main error message to display
        error_details: Optional dictionary of error details to show (e.g., `{"Error Code": "invalid_client"}`)
        server_name: Optional server name to display
        server_icon_url: Optional URL to server icon/logo

    Returns:
        Complete HTML page as a string
    """
    import html as html_module

    error_message_escaped = html_module.escape(error_message)

    # Build error message box
    error_box = f"""
        <div class="info-box error">
            <p>{error_message_escaped}</p>
        </div>
    """

    # Build error details section if provided
    details_section = ""
    if error_details:
        detail_rows_html = "\n".join(
            [
                f"""
            <div class="detail-row">
                <div class="detail-label">{html_module.escape(label)}:</div>
                <div class="detail-value">{html_module.escape(value)}</div>
            </div>
            """
                for label, value in error_details.items()
            ]
        )

        details_section = f"""
            <details>
                <summary>Error Details</summary>
                <div class="detail-box">
                    {detail_rows_html}
                </div>
            </details>
        """

    # Build the page content
    content = f"""
        <div class="container">
            {create_logo(icon_url=server_icon_url, alt_text=server_name or "FastMCP")}
            <h1>{html_module.escape(error_title)}</h1>
            {error_box}
            {details_section}
        </div>
    """

    # Additional styles needed for this page
    # Override .info-box.error to use normal text color instead of red
    additional_styles = (
        INFO_BOX_STYLES
        + DETAILS_STYLES
        + DETAIL_BOX_STYLES
        + """
        .info-box.error {
            color: #111827;
        }
        """
    )

    # Simple CSP policy for error pages (no forms needed)
    csp_policy = "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'"

    return create_page(
        content=content,
        title=error_title,
        additional_styles=additional_styles,
        csp_policy=csp_policy,
    )
