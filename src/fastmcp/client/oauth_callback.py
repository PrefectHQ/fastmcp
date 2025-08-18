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
from starlette.responses import HTMLResponse
from starlette.routing import Route
from uvicorn import Config, Server

from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def create_callback_html(
    message: str,
    is_success: bool = True,
    title: str = "FastMCP OAuth",
    server_url: str | None = None,
) -> str:
    """Create a styled HTML response for OAuth callbacks."""
    logo_url = "https://gofastmcp.com/assets/brand/blue-logo.png"

    # Build the main status message
    if is_success:
        status_title = "Authentication successful"
        status_icon = "✓"
        icon_bg = "#10b98120"
    else:
        status_title = "Authentication failed"
        status_icon = "✕"
        icon_bg = "#ef444420"

    # Add detail info box for both success and error cases
    detail_info = ""
    if is_success and server_url:
        detail_info = f"""
            <div class="info-box">
                Connected to: <strong>{server_url}</strong>
            </div>
        """
    elif not is_success:
        detail_info = f"""
            <div class="info-box error">
                {message}
            </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                margin: 0;
                padding: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #ffffff;
                color: #0a0a0a;
            }}
            
            .container {{
                background: #ffffff;
                border: 1px solid #e5e5e5;
                padding: 3rem 2rem;
                border-radius: 0.75rem;
                box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px -1px rgba(0, 0, 0, 0.1);
                text-align: center;
                max-width: 28rem;
                margin: 1rem;
                position: relative;
            }}
            
            .logo {{
                width: 60px;
                height: auto;
                margin-bottom: 2rem;
                display: block;
                margin-left: auto;
                margin-right: auto;
            }}
            
            .status-message {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.75rem;
                margin-bottom: 1.5rem;
            }}
            
            .status-icon {{
                font-size: 1.5rem;
                line-height: 1;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 2rem;
                height: 2rem;
                background: {icon_bg};
                border-radius: 0.5rem;
                flex-shrink: 0;
            }}
            
            .message {{
                font-size: 1.125rem;
                line-height: 1.75;
                color: #0a0a0a;
                font-weight: 600;
                text-align: left;
            }}
            
            .info-box {{
                background: #f5f5f5;
                border: 1px solid #e5e5e5;
                border-radius: 0.5rem;
                padding: 0.875rem;
                margin: 1.25rem 0;
                font-size: 0.875rem;
                color: #525252;
                font-family: 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
                text-align: left;
            }}
            
            .info-box.error {{
                background: #fef2f2;
                border-color: #fecaca;
                color: #991b1b;
            }}
            
            .info-box strong {{
                color: #0a0a0a;
                font-weight: 600;
            }}
            
            .close-instruction {{
                font-size: 0.875rem;
                color: #737373;
                margin-top: 1.5rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <img src="{logo_url}" alt="FastMCP" class="logo" />
            <div class="status-message">
                <span class="status-icon">{status_icon}</span>
                <div class="message">{status_title}</div>
            </div>
            {detail_info}
            <div class="close-instruction">
                You can safely close this tab now.
            </div>
        </div>
    </body>
    </html>
    """


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

            # Resolve future with exception if provided
            if response_future and not response_future.done():
                response_future.set_exception(
                    RuntimeError(
                        f"OAuth error: {callback_response.error} - {error_desc}"
                    )
                )

            return HTMLResponse(
                create_callback_html(
                    f"FastMCP OAuth Error: {callback_response.error}<br>{error_desc}",
                    is_success=False,
                ),
                status_code=400,
            )

        if not callback_response.code:
            # Resolve future with exception if provided
            if response_future and not response_future.done():
                response_future.set_exception(
                    RuntimeError("OAuth callback missing authorization code")
                )

            return HTMLResponse(
                create_callback_html(
                    "FastMCP OAuth Error: No authorization code received",
                    is_success=False,
                ),
                status_code=400,
            )

        # Check for missing state parameter (indicates OAuth flow issue)
        if callback_response.state is None:
            # Resolve future with exception if provided
            if response_future and not response_future.done():
                response_future.set_exception(
                    RuntimeError(
                        "OAuth server did not return state parameter - authentication failed"
                    )
                )

            return HTMLResponse(
                create_callback_html(
                    "FastMCP OAuth Error: Authentication failed<br>The OAuth server did not return the expected state parameter",
                    is_success=False,
                ),
                status_code=400,
            )

        # Success case
        if response_future and not response_future.done():
            response_future.set_result(
                (callback_response.code, callback_response.state)
            )

        return HTMLResponse(
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
