"""Enhanced authorization handler with improved error responses.

This module provides an enhanced authorization handler that wraps the MCP SDK's
AuthorizationHandler to provide better error messages when clients attempt to
authorize with unregistered client IDs.

The enhancement adds:
- Content negotiation: HTML for browsers, JSON for API clients
- Enhanced JSON responses with registration endpoint hints
- Styled HTML error pages with registration links/forms
- Link headers pointing to registration endpoints
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.auth.handlers.authorize import (
    AuthorizationHandler as SDKAuthorizationHandler,
)
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import Response

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import (
    INFO_BOX_STYLES,
    TOOLTIP_STYLES,
    create_logo,
    create_page,
    create_secure_html_response,
)

if TYPE_CHECKING:
    from mcp.server.auth.provider import OAuthAuthorizationServerProvider

logger = get_logger(__name__)


def create_unregistered_client_html(
    client_id: str,
    registration_endpoint: str,
    discovery_endpoint: str,
    server_name: str | None = None,
    server_icon_url: str | None = None,
    title: str = "Client Not Registered",
) -> str:
    """Create styled HTML error page for unregistered client attempts.

    Args:
        client_id: The unregistered client ID that was provided
        registration_endpoint: URL of the registration endpoint
        discovery_endpoint: URL of the OAuth metadata discovery endpoint
        server_name: Optional server name for branding
        server_icon_url: Optional server icon URL
        title: Page title

    Returns:
        HTML string for the error page
    """
    import html as html_module

    client_id_escaped = html_module.escape(client_id)

    # Main error message
    error_box = f"""
        <div class="info-box error">
            <p>The client ID <code>{client_id_escaped}</code> was not found in the server's client registry.</p>
        </div>
    """

    # What to do - yellow warning box
    warning_box = """
        <div class="info-box warning">
            <p>Your MCP client opened this page to complete OAuth authorization,
            but the server did not recognize its client ID. To fix this:</p>
            <ul>
                <li>Close this browser window</li>
                <li>Clear authentication tokens in your MCP client (or restart it)</li>
                <li>Try connecting again - your client should automatically re-register</li>
            </ul>
        </div>
    """

    # Help link with tooltip (similar to consent screen)
    help_link = """
        <div class="help-link-container">
            <span class="help-link">
                Why am I seeing this?
                <span class="tooltip">
                    OAuth 2.0 requires clients to register before authorization.
                    This server returned a 400 error because the provided client
                    ID was not found.
                    <br><br>
                    In browser-delegated OAuth flows, your application cannot
                    detect this error automatically; it's waiting for a
                    callback that will never arrive. You must manually clear
                    auth tokens and reconnect.
                </span>
            </span>
        </div>
    """

    # Build page content
    content = f"""
        <div class="container">
            {create_logo(icon_url=server_icon_url, alt_text=server_name or "FastMCP")}
            <h1>{title}</h1>
            {error_box}
            {warning_box}
        </div>
        {help_link}
    """

    # Use same styles as consent page
    additional_styles = (
        INFO_BOX_STYLES
        + TOOLTIP_STYLES
        + """
        /* Error variant for info-box */
        .info-box.error {
            background: #fef2f2;
            border-color: #f87171;
        }
        .info-box.error strong {
            color: #991b1b;
        }
        /* Warning variant for info-box (yellow) */
        .info-box.warning {
            background: #fffbeb;
            border-color: #fbbf24;
        }
        .info-box.warning strong {
            color: #92400e;
        }
        .info-box code {
            background: rgba(0, 0, 0, 0.05);
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
            font-size: 0.9em;
        }
        .info-box ul {
            margin: 10px 0;
            padding-left: 20px;
        }
        .info-box li {
            margin: 6px 0;
        }
        """
    )

    return create_page(
        content=content,
        title=title,
        additional_styles=additional_styles,
    )


class AuthorizationHandler(SDKAuthorizationHandler):
    """Authorization handler with enhanced error responses for unregistered clients.

    This handler extends the MCP SDK's AuthorizationHandler to provide better UX
    when clients attempt to authorize without being registered. It implements
    content negotiation to return:

    - HTML error pages for browser requests
    - Enhanced JSON with registration hints for API clients
    - Link headers pointing to registration endpoints

    This maintains OAuth 2.1 compliance (returns 400 for invalid client_id)
    while providing actionable guidance to fix the error.
    """

    def __init__(
        self,
        provider: OAuthAuthorizationServerProvider,
        base_url: AnyHttpUrl | str,
        server_name: str | None = None,
        server_icon_url: str | None = None,
    ):
        """Initialize the enhanced authorization handler.

        Args:
            provider: OAuth authorization server provider
            base_url: Base URL of the server for constructing endpoint URLs
            server_name: Optional server name for branding
            server_icon_url: Optional server icon URL for branding
        """
        super().__init__(provider)
        self._base_url = str(base_url).rstrip("/")
        self._server_name = server_name
        self._server_icon_url = server_icon_url

    async def handle(self, request: Request) -> Response:
        """Handle authorization request with enhanced error responses.

        This method extends the SDK's authorization handler and intercepts
        errors for unregistered clients to provide better error responses
        based on the client's Accept header.

        Args:
            request: The authorization request

        Returns:
            Response (redirect on success, error response on failure)
        """
        # Call the SDK handler
        response = await super().handle(request)

        # Check if this is a client not found error
        if response.status_code == 400:
            # Try to extract client_id from request for enhanced error
            client_id = None
            if request.method == "GET":
                client_id = request.query_params.get("client_id")
            else:
                form = await request.form()
                client_id = form.get("client_id")

            # If we have a client_id and the error is about it not being found,
            # enhance the response
            if client_id:
                try:
                    # Check if response body contains "not found" error
                    if hasattr(response, "body"):
                        import json

                        body = json.loads(response.body)
                        if (
                            body.get("error") == "invalid_request"
                            and "not found" in body.get("error_description", "").lower()
                        ):
                            return await self._create_enhanced_error_response(
                                request, client_id, body.get("state")
                            )
                except Exception:
                    # If we can't parse the response, just return the original
                    pass

        return response

    async def _create_enhanced_error_response(
        self, request: Request, client_id: str, state: str | None
    ) -> Response:
        """Create enhanced error response with content negotiation.

        Args:
            request: The original request
            client_id: The unregistered client ID
            state: The state parameter from the request

        Returns:
            HTML or JSON error response based on Accept header
        """
        registration_endpoint = f"{self._base_url}/register"
        discovery_endpoint = f"{self._base_url}/.well-known/oauth-authorization-server"

        # Extract server metadata from app state (same pattern as consent screen)
        from fastmcp.server.server import FastMCP

        fastmcp = getattr(request.app.state, "fastmcp_server", None)

        if isinstance(fastmcp, FastMCP):
            server_name = fastmcp.name
            icons = fastmcp.icons
            server_icon_url = icons[0].src if icons else None
        else:
            server_name = self._server_name
            server_icon_url = self._server_icon_url

        # Check Accept header for content negotiation
        accept = request.headers.get("accept", "")

        # Prefer HTML for browsers
        if "text/html" in accept:
            html = create_unregistered_client_html(
                client_id=client_id,
                registration_endpoint=registration_endpoint,
                discovery_endpoint=discovery_endpoint,
                server_name=server_name,
                server_icon_url=server_icon_url,
            )
            response = create_secure_html_response(html, status_code=400)
        else:
            # Return enhanced JSON for API clients
            from mcp.server.auth.handlers.authorize import AuthorizationErrorResponse

            error_data = AuthorizationErrorResponse(
                error="invalid_request",
                error_description=(
                    f"Client ID '{client_id}' is not registered with this server. "
                    f"MCP clients should automatically re-register by sending a POST request to "
                    f"the registration_endpoint and retry authorization. "
                    f"If this persists, clear cached authentication tokens and reconnect."
                ),
                state=state,
            )

            # Add extra fields to help clients discover registration
            error_dict = error_data.model_dump(exclude_none=True)
            error_dict["registration_endpoint"] = registration_endpoint
            error_dict["authorization_server_metadata"] = discovery_endpoint

            from starlette.responses import JSONResponse

            response = JSONResponse(
                status_code=400,
                content=error_dict,
                headers={"Cache-Control": "no-store"},
            )

        # Add Link header for registration endpoint discovery
        response.headers["Link"] = (
            f'<{registration_endpoint}>; rel="http://oauth.net/core/2.1/#registration"'
        )

        logger.info(
            "Unregistered client_id=%s, returned %s error response",
            client_id,
            "HTML" if "text/html" in accept else "JSON",
        )

        return response
