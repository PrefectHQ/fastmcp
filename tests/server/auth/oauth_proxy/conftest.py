"""Shared fixtures and helpers for OAuth proxy tests."""

import asyncio
import secrets
import time
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier


class MockOAuthProvider:
    """Mock OAuth provider for testing OAuth proxy E2E flows.

    This provider simulates a complete OAuth server without requiring:
    - Real authentication credentials
    - Browser automation
    - Network calls to external services
    """

    def __init__(self, port: int = 0):
        self.port = port
        self.base_url = f"http://localhost:{port}"
        self.app = None
        self.server = None

        # Storage for OAuth state
        self.authorization_codes = {}
        self.access_tokens = {}
        self.refresh_tokens = {}
        self.revoked_tokens = set()

        # Tracking for assertions
        self.authorize_called = False
        self.token_called = False
        self.refresh_called = False
        self.revoke_called = False

        # Configuration
        self.require_pkce = False
        self.token_endpoint_auth_method = "client_secret_basic"

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.base_url}/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.base_url}/token"

    @property
    def revocation_endpoint(self) -> str:
        return f"{self.base_url}/revoke"

    def create_app(self) -> Starlette:
        """Create the mock OAuth server application."""
        return Starlette(
            routes=[
                Route("/authorize", self.handle_authorize),
                Route("/token", self.handle_token, methods=["POST"]),
                Route("/revoke", self.handle_revoke, methods=["POST"]),
            ]
        )

    async def handle_authorize(self, request):
        """Handle authorization requests."""
        self.authorize_called = True
        query = dict(request.query_params)

        # Validate PKCE if required
        if self.require_pkce and "code_challenge" not in query:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "PKCE required"},
                status_code=400,
            )

        # Generate authorization code
        code = secrets.token_urlsafe(32)
        self.authorization_codes[code] = {
            "client_id": query.get("client_id"),
            "redirect_uri": query.get("redirect_uri"),
            "state": query.get("state"),
            "code_challenge": query.get("code_challenge"),
            "code_challenge_method": query.get("code_challenge_method", "S256"),
            "scope": query.get("scope"),
            "created_at": time.time(),
        }

        # Redirect back to callback
        redirect_uri = query["redirect_uri"]
        params = {"code": code}
        if query.get("state"):
            params["state"] = query["state"]

        redirect_url = f"{redirect_uri}?{urlencode(params)}"
        return JSONResponse(
            content={}, status_code=302, headers={"Location": redirect_url}
        )

    async def handle_token(self, request):
        """Handle token requests."""
        self.token_called = True
        form = await request.form()
        grant_type = form.get("grant_type")

        if grant_type == "authorization_code":
            code = form.get("code")
            if code not in self.authorization_codes:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid code"},
                    status_code=400,
                )

            # Validate PKCE if it was used
            auth_data = self.authorization_codes[code]
            if auth_data.get("code_challenge"):
                verifier = form.get("code_verifier")
                if not verifier:
                    return JSONResponse(
                        {
                            "error": "invalid_request",
                            "error_description": "Missing code_verifier",
                        },
                        status_code=400,
                    )
                # In a real implementation, we'd validate the verifier

            # Generate tokens
            access_token = f"mock_access_{secrets.token_hex(16)}"
            refresh_token = f"mock_refresh_{secrets.token_hex(16)}"

            self.access_tokens[access_token] = {
                "client_id": auth_data["client_id"],
                "scope": auth_data.get("scope"),
                "expires_at": time.time() + 3600,
            }
            self.refresh_tokens[refresh_token] = {
                "client_id": auth_data["client_id"],
                "scope": auth_data.get("scope"),
            }

            # Clean up used code
            del self.authorization_codes[code]

            return JSONResponse(
                {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": refresh_token,
                    "scope": auth_data.get("scope"),
                }
            )

        elif grant_type == "refresh_token":
            self.refresh_called = True
            refresh_token = form.get("refresh_token")

            if refresh_token not in self.refresh_tokens:
                return JSONResponse(
                    {
                        "error": "invalid_grant",
                        "error_description": "Invalid refresh token",
                    },
                    status_code=400,
                )

            # Generate new access token
            new_access = f"mock_access_{secrets.token_hex(16)}"
            token_data = self.refresh_tokens[refresh_token]

            self.access_tokens[new_access] = {
                "client_id": token_data["client_id"],
                "scope": token_data.get("scope"),
                "expires_at": time.time() + 3600,
            }

            return JSONResponse(
                {
                    "access_token": new_access,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": refresh_token,  # Same refresh token
                    "scope": token_data.get("scope"),
                }
            )

        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def handle_revoke(self, request):
        """Handle token revocation."""
        self.revoke_called = True
        form = await request.form()
        token = form.get("token")

        if token:
            self.revoked_tokens.add(token)
            # Remove from active tokens
            self.access_tokens.pop(token, None)
            self.refresh_tokens.pop(token, None)

        return JSONResponse({})

    async def start(self):
        """Start the mock OAuth server."""
        import socket

        from uvicorn import Config, Server

        self.app = self.create_app()

        # If port is 0, find an available port
        if self.port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                self.port = s.getsockname()[1]

        self.base_url = f"http://localhost:{self.port}"
        config = Config(
            self.app,
            host="localhost",
            port=self.port,
            log_level="error",
            ws="websockets-sansio",
        )
        self.server = Server(config)

        # Start server in background
        asyncio.create_task(self.server.serve())

        # Wait for server to be ready
        await asyncio.sleep(0.05)

    async def stop(self):
        """Stop the mock OAuth server."""
        if self.server:
            self.server.should_exit = True
            await asyncio.sleep(0.01)

    def reset(self):
        """Reset all state for next test."""
        self.authorization_codes.clear()
        self.access_tokens.clear()
        self.refresh_tokens.clear()
        self.revoked_tokens.clear()
        self.authorize_called = False
        self.token_called = False
        self.refresh_called = False
        self.revoke_called = False


class MockTokenVerifier(TokenVerifier):
    """Mock token verifier for testing."""

    def __init__(self, required_scopes=None):
        self.required_scopes = required_scopes or ["read", "write"]
        self.verify_called = False

    async def verify_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        """Mock token verification."""
        self.verify_called = True
        return AccessToken(
            token=token,
            client_id="mock-client",
            scopes=self.required_scopes,
            expires_at=int(time.time() + 3600),
        )


@pytest.fixture
def jwt_verifier():
    """Create a mock JWT verifier for testing."""
    verifier = Mock(spec=JWTVerifier)
    verifier.required_scopes = ["read", "write"]
    verifier.verify_token = Mock(return_value=None)
    return verifier


@pytest.fixture
def oauth_proxy(jwt_verifier):
    """Create a standard OAuthProxy instance for testing."""
    return OAuthProxy(
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id="test-client-id",
        upstream_client_secret="test-client-secret",
        token_verifier=jwt_verifier,
        base_url="https://myserver.com",
        redirect_path="/auth/callback",
        jwt_signing_key="test-secret",
    )


@pytest.fixture
async def mock_oauth_provider():
    """Create and start a mock OAuth provider."""
    provider = MockOAuthProvider()
    await provider.start()
    yield provider
    await provider.stop()
