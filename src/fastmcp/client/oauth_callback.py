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
    status_emoji = "✅" if is_success else "❌"
    status_color = "#10b981" if is_success else "#ef4444"  # emerald-500 / red-500

    # Add server info for success cases
    server_info = ""
    if is_success and server_url:
        server_info = f"""
            <div class="server-info">
                Connected to: <strong>{server_url}</strong>
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
            body {{
                font-family: 'SF Mono', 'Monaco', 'Consolas', 'Roboto Mono', monospace;
                margin: 0;
                padding: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 25%, #16213e 50%, #0f0f23 100%);
                color: #e2e8f0;
                overflow: hidden;
            }}
            
            body::before {{
                content: '';
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: 
                    radial-gradient(circle at 20% 80%, rgba(120, 119, 198, 0.1) 0%, transparent 50%),
                    radial-gradient(circle at 80% 20%, rgba(16, 185, 129, 0.1) 0%, transparent 50%),
                    radial-gradient(circle at 40% 40%, rgba(14, 165, 233, 0.1) 0%, transparent 50%);
                pointer-events: none;
                z-index: -1;
            }}
            
            .container {{
                background: rgba(30, 41, 59, 0.9);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(71, 85, 105, 0.3);
                padding: 3rem 2rem;
                border-radius: 1rem;
                box-shadow: 
                    0 25px 50px -12px rgba(0, 0, 0, 0.7),
                    0 0 0 1px rgba(255, 255, 255, 0.05),
                    inset 0 1px 0 0 rgba(255, 255, 255, 0.1);
                text-align: center;
                max-width: 500px;
                margin: 1rem;
                position: relative;
            }}
            
            .container::before {{
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                height: 1px;
                background: linear-gradient(90deg, transparent, rgba(16, 185, 129, 0.5), transparent);
            }}
            
            .status-icon {{
                font-size: 4rem;
                margin-bottom: 1rem;
                display: block;
                filter: drop-shadow(0 0 20px currentColor);
            }}
            
            .message {{
                font-size: 1.25rem;
                line-height: 1.6;
                color: {status_color};
                margin-bottom: 1.5rem;
                font-weight: 600;
                text-shadow: 0 0 10px rgba({
        "16, 185, 129" if is_success else "239, 68, 68"
    }, 0.3);
            }}
            
            .server-info {{
                background: rgba(6, 182, 212, 0.1);
                border: 1px solid rgba(6, 182, 212, 0.3);
                border-radius: 0.75rem;
                padding: 1rem;
                margin: 1rem 0;
                font-size: 0.9rem;
                color: #67e8f9;
                font-family: 'SF Mono', 'Monaco', 'Consolas', 'Roboto Mono', monospace;
                text-shadow: 0 0 10px rgba(103, 232, 249, 0.3);
            }}
            
            .server-info strong {{
                color: #22d3ee;
                font-weight: 700;
            }}
            
            .subtitle {{
                font-size: 1rem;
                color: #94a3b8;
                margin-top: 1rem;
            }}
            
            .close-instruction {{
                background: rgba(51, 65, 85, 0.8);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 0.75rem;
                padding: 1rem;
                margin-top: 1.5rem;
                font-size: 0.9rem;
                color: #cbd5e1;
                font-family: 'SF Mono', 'Monaco', 'Consolas', 'Roboto Mono', monospace;
            }}
            
            @keyframes glow {{
                0%, 100% {{ opacity: 1; }}
                50% {{ opacity: 0.7; }}
            }}
            
            .status-icon {{
                animation: glow 2s ease-in-out infinite;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <span class="status-icon">{status_emoji}</span>
            <div class="message">{message}</div>
            {server_info}
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
            create_callback_html("FastMCP OAuth login complete!", server_url=server_url)
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
