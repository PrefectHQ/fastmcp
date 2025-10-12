"""
OAuth callback server for handling authorization code flows.

This module provides a reusable callback server that can handle OAuth redirects
and display styled responses to users.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from uvicorn import Config, Server

from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.ui import (
    HELPER_TEXT_STYLES,
    INFO_BOX_STYLES,
    STATUS_MESSAGE_STYLES,
    create_info_box,
    create_logo,
    create_page,
    create_secure_html_response,
    create_status_message,
)

logger = get_logger(__name__)


def create_callback_html(
    message: str,
    is_success: bool = True,
    title: str = "FastMCP OAuth",
    server_url: str | None = None,
) -> str:
    """Create a styled HTML response for OAuth callbacks."""
    # Build the main status message
    status_title = (
        "Authentication successful" if is_success else "Authentication failed"
    )

    # Add detail info box for both success and error cases
    detail_info = ""
    if is_success and server_url:
        detail_info = create_info_box(
            f"Connected to: <strong>{server_url}</strong>", centered=True
        )
    elif not is_success:
        detail_info = create_info_box(message, is_error=True, centered=True)

    # Build the page content
    content = f"""
        <div class="container">
            {create_logo()}
            {create_status_message(status_title, is_success=is_success)}
            {detail_info}
            <div class="close-instruction">
                You can safely close this tab now.
            </div>
        </div>
    """

    # Additional styles needed for this page
    additional_styles = STATUS_MESSAGE_STYLES + INFO_BOX_STYLES + HELPER_TEXT_STYLES

    return create_page(
        content=content,
        title=title,
        additional_styles=additional_styles,
    )


@dataclass
class CallbackResponse:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> CallbackResponse:
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def create_oauth_callback_server(
    port: int,
    callback_path: str = "/callback",
    server_url: str | None = None,
    response_future: asyncio.Future | None = None,
) -> Server:
    """
    Create an OAuth callback server.

    Args:
        port: The port to run the server on
        callback_path: The path to listen for OAuth redirects on
        server_url: Optional server URL to display in success messages
        response_future: Optional future to resolve when OAuth callback is received

    Returns:
        Configured uvicorn Server instance (not yet running)
    """

    async def callback_handler(request: Request):
        """Handle OAuth callback requests with proper HTML responses."""
        query_params = dict(request.query_params)
        callback_response = CallbackResponse.from_dict(query_params)

        if callback_response.error:
            error_desc = callback_response.error_description or "Unknown error"

            # Create user-friendly error messages
            if callback_response.error == "access_denied":
                user_message = "Access was denied by the authorization server."
            else:
                user_message = f"Authorization failed: {error_desc}"

            # Resolve future with exception if provided
            if response_future and not response_future.done():
                response_future.set_exception(RuntimeError(user_message))

            return create_secure_html_response(
                create_callback_html(
                    user_message,
                    is_success=False,
                ),
                status_code=400,
            )

        if not callback_response.code:
            user_message = "No authorization code was received from the server."

            # Resolve future with exception if provided
            if response_future and not response_future.done():
                response_future.set_exception(RuntimeError(user_message))

            return create_secure_html_response(
                create_callback_html(
                    user_message,
                    is_success=False,
                ),
                status_code=400,
            )

        # Check for missing state parameter (indicates OAuth flow issue)
        if callback_response.state is None:
            user_message = (
                "The OAuth server did not return the expected state parameter."
            )

            # Resolve future with exception if provided
            if response_future and not response_future.done():
                response_future.set_exception(RuntimeError(user_message))

            return create_secure_html_response(
                create_callback_html(
                    user_message,
                    is_success=False,
                ),
                status_code=400,
            )

        # Success case
        if response_future and not response_future.done():
            response_future.set_result(
                (callback_response.code, callback_response.state)
            )

        return create_secure_html_response(
            create_callback_html("", is_success=True, server_url=server_url)
        )

    app = Starlette(routes=[Route(callback_path, callback_handler)])

    return Server(
        Config(
            app=app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="warning",
            ws="websockets-sansio",
        )
    )


if __name__ == "__main__":
    """Run a test server when executed directly."""
    import webbrowser

    import uvicorn

    port = find_available_port()
    print("🎭 OAuth Callback Test Server")
    print("📍 Test URLs:")
    print(f"  Success: http://localhost:{port}/callback?code=test123&state=xyz")
    print(
        f"  Error:   http://localhost:{port}/callback?error=access_denied&error_description=User%20denied"
    )
    print(f"  Missing: http://localhost:{port}/callback")
    print("🛑 Press Ctrl+C to stop")
    print()

    # Create test server without future (just for testing HTML responses)
    server = create_oauth_callback_server(
        port=port, server_url="https://fastmcp-test-server.example.com"
    )

    # Open browser to success example
    webbrowser.open(f"http://localhost:{port}/callback?code=test123&state=xyz")

    # Run with uvicorn directly
    uvicorn.run(
        server.config.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
