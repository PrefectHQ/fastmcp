import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.routes import (
    create_auth_routes,
)
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthToken,
)
from pydantic import AnyHttpUrl
from starlette.applications import Starlette


# Mock OAuth provider for testing
class MockOAuthProvider(OAuthAuthorizationServerProvider):
    def __init__(self):
        self.clients = {}
        self.auth_codes = {}  # code -> {client_id, code_challenge, redirect_uri}
        self.tokens = {}  # token -> {client_id, scopes, expires_at}
        self.refresh_tokens = {}  # refresh_token -> access_token

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull):
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # toy authorize implementation which just immediately generates an authorization
        # code and completes the redirect
        if client.client_id is None:
            raise ValueError("client_id is required")
        code = AuthorizationCode(
            code=f"code_{int(time.time())}",
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            expires_at=time.time() + 300,
            scopes=params.scopes or ["read", "write"],
        )
        self.auth_codes[code.code] = code

        return construct_redirect_uri(
            str(params.redirect_uri), code=code.code, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        assert authorization_code.code in self.auth_codes

        # Generate an access token and refresh token
        access_token = f"access_{secrets.token_hex(32)}"
        refresh_token = f"refresh_{secrets.token_hex(32)}"

        # Store the tokens
        if client.client_id is None:
            raise ValueError("client_id is required")
        self.tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + 3600,
        )

        self.refresh_tokens[refresh_token] = access_token

        # Remove the used code
        del self.auth_codes[authorization_code.code]

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=3600,
            scope="read write",
            refresh_token=refresh_token,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        old_access_token = self.refresh_tokens.get(refresh_token)
        if old_access_token is None:
            return None
        token_info = self.tokens.get(old_access_token)
        if token_info is None:
            return None

        # Create a RefreshToken object that matches what is expected in later code
        refresh_obj = RefreshToken(
            token=refresh_token,
            client_id=token_info.client_id,
            scopes=token_info.scopes,
            expires_at=token_info.expires_at,
        )

        return refresh_obj

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Check if refresh token exists
        assert refresh_token.token in self.refresh_tokens

        old_access_token = self.refresh_tokens[refresh_token.token]

        # Check if the access token exists
        assert old_access_token in self.tokens

        # Check if the token was issued to this client
        token_info = self.tokens[old_access_token]
        assert token_info.client_id == client.client_id

        # Generate a new access token and refresh token
        new_access_token = f"access_{secrets.token_hex(32)}"
        new_refresh_token = f"refresh_{secrets.token_hex(32)}"

        # Store the new tokens
        if client.client_id is None:
            raise ValueError("client_id is required")
        self.tokens[new_access_token] = AccessToken(
            token=new_access_token,
            client_id=client.client_id,
            scopes=scopes or token_info.scopes,
            expires_at=int(time.time()) + 3600,
        )

        self.refresh_tokens[new_refresh_token] = new_access_token

        # Remove the old tokens
        del self.refresh_tokens[refresh_token.token]
        del self.tokens[old_access_token]

        return OAuthToken(
            access_token=new_access_token,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(scopes) if scopes else " ".join(token_info.scopes),
            refresh_token=new_refresh_token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        token_info = self.tokens.get(token)

        return token_info and AccessToken(
            token=token,
            client_id=token_info.client_id,
            scopes=token_info.scopes,
            expires_at=token_info.expires_at,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        match token:
            case RefreshToken():
                # Remove the refresh token
                del self.refresh_tokens[token.token]

            case AccessToken():
                # Remove the access token
                del self.tokens[token.token]

                # Also remove any refresh tokens that point to this access token
                for refresh_token, access_token in list(self.refresh_tokens.items()):
                    if access_token == token.token:
                        del self.refresh_tokens[refresh_token]


@pytest.fixture
def mock_oauth_provider():
    return MockOAuthProvider()


@pytest.fixture
def auth_app(mock_oauth_provider):
    # Create auth router
    auth_routes = create_auth_routes(
        mock_oauth_provider,
        AnyHttpUrl("https://auth.example.com"),
        AnyHttpUrl("https://docs.example.com"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["read", "write", "profile"],
            default_scopes=["read", "write"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )

    # Create Starlette app
    app = Starlette(routes=auth_routes)

    return app


@pytest.fixture
async def test_client(auth_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://mcptest.com"
    ) as client:
        yield client


@pytest.fixture
async def registered_client(test_client: httpx.AsyncClient, request):
    """Create and register a test client.

    Parameters can be customized via indirect parameterization:
    @pytest.mark.parametrize("registered_client",
                            [{"grant_types": ["authorization_code"]}],
                            indirect=True)
    """
    # Default client metadata
    client_metadata = {
        "redirect_uris": ["https://client.example.com/callback"],
        "client_name": "Test Client",
        "grant_types": ["authorization_code", "refresh_token"],
    }

    # Override with any parameters from the test
    if hasattr(request, "param") and request.param:
        client_metadata.update(request.param)

    response = await test_client.post("/register", json=client_metadata)
    assert response.status_code == 201, f"Failed to register client: {response.content}"

    client_info = response.json()
    return client_info


@pytest.fixture
def pkce_challenge():
    """Create a PKCE challenge with code_verifier and code_challenge."""
    code_verifier = "some_random_verifier_string"
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )

    return {"code_verifier": code_verifier, "code_challenge": code_challenge}


class TestAuthorizeEndpointErrors:
    """Test error handling in the OAuth authorization endpoint."""

    async def test_authorize_missing_client_id(
        self, test_client: httpx.AsyncClient, pkce_challenge
    ):
        """Test authorization endpoint with missing client_id.

        According to the OAuth2.0 spec, if client_id is missing, the server should
        inform the resource owner and NOT redirect.
        """
        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                # Missing client_id
                "redirect_uri": "https://client.example.com/callback",
                "state": "test_state",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
            },
        )

        # Should NOT redirect, should show an error page
        assert response.status_code == 400
        # The response should include an error message about missing client_id
        assert "client_id" in response.text.lower()

    async def test_authorize_invalid_client_id(
        self, test_client: httpx.AsyncClient, pkce_challenge
    ):
        """Test authorization endpoint with invalid client_id.

        According to the OAuth2.0 spec, if client_id is invalid, the server should
        inform the resource owner and NOT redirect.
        """
        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "invalid_client_id_that_does_not_exist",
                "redirect_uri": "https://client.example.com/callback",
                "state": "test_state",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
            },
        )

        # Should NOT redirect, should show an error page
        assert response.status_code == 400
        # The response should include an error message about invalid client_id
        assert "client" in response.text.lower()

    async def test_authorize_missing_redirect_uri(
        self, test_client: httpx.AsyncClient, registered_client, pkce_challenge
    ):
        """Test authorization endpoint with missing redirect_uri.

        If client has only one registered redirect_uri, it can be omitted.
        """

        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered_client["client_id"],
                # Missing redirect_uri
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_state",
            },
        )

        # Should redirect to the registered redirect_uri
        assert response.status_code == 302, response.content
        redirect_url = response.headers["location"]
        assert redirect_url.startswith("https://client.example.com/callback")

    async def test_authorize_invalid_redirect_uri(
        self, test_client: httpx.AsyncClient, registered_client, pkce_challenge
    ):
        """Test authorization endpoint with invalid redirect_uri.

        According to the OAuth2.0 spec, if redirect_uri is invalid or doesn't match,
        the server should inform the resource owner and NOT redirect.
        """

        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered_client["client_id"],
                # Non-matching URI
                "redirect_uri": "https://attacker.example.com/callback",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_state",
            },
        )

        # Should NOT redirect, should show an error page
        assert response.status_code == 400, response.content
        # The response should include an error message about redirect_uri mismatch
        assert "redirect" in response.text.lower()

    @pytest.mark.parametrize(
        "registered_client",
        [
            {
                "redirect_uris": [
                    "https://client.example.com/callback",
                    "https://client.example.com/other-callback",
                ]
            }
        ],
        indirect=True,
    )
    async def test_authorize_missing_redirect_uri_multiple_registered(
        self, test_client: httpx.AsyncClient, registered_client, pkce_challenge
    ):
        """Test endpoint with missing redirect_uri with multiple registered URIs.

        If client has multiple registered redirect_uris, redirect_uri must be provided.
        """

        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered_client["client_id"],
                # Missing redirect_uri
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_state",
            },
        )

        # Should NOT redirect, should return a 400 error
        assert response.status_code == 400
        # The response should include an error message about missing redirect_uri
        assert "redirect_uri" in response.text.lower()

    async def test_authorize_unsupported_response_type(
        self, test_client: httpx.AsyncClient, registered_client, pkce_challenge
    ):
        """Test authorization endpoint with unsupported response_type.

        According to the OAuth2.0 spec, for other errors like unsupported_response_type,
        the server should redirect with error parameters.
        """

        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "token",  # Unsupported (we only support "code")
                "client_id": registered_client["client_id"],
                "redirect_uri": "https://client.example.com/callback",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_state",
            },
        )

        # Should redirect with error parameters
        assert response.status_code == 302
        redirect_url = response.headers["location"]
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)

        assert "error" in query_params
        assert query_params["error"][0] == "unsupported_response_type"
        # State should be preserved
        assert "state" in query_params
        assert query_params["state"][0] == "test_state"

    async def test_authorize_missing_response_type(
        self, test_client: httpx.AsyncClient, registered_client, pkce_challenge
    ):
        """Test authorization endpoint with missing response_type.

        Missing required parameter should result in invalid_request error.
        """

        response = await test_client.get(
            "/authorize",
            params={
                # Missing response_type
                "client_id": registered_client["client_id"],
                "redirect_uri": "https://client.example.com/callback",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_state",
            },
        )

        # Should redirect with error parameters
        assert response.status_code == 302
        redirect_url = response.headers["location"]
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)

        assert "error" in query_params
        assert query_params["error"][0] == "invalid_request"
        # State should be preserved
        assert "state" in query_params
        assert query_params["state"][0] == "test_state"

    async def test_authorize_missing_pkce_challenge(
        self, test_client: httpx.AsyncClient, registered_client
    ):
        """Test authorization endpoint with missing PKCE code_challenge.

        Missing PKCE parameters should result in invalid_request error.
        """
        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered_client["client_id"],
                # Missing code_challenge
                "state": "test_state",
                # using default URL
            },
        )

        # Should redirect with error parameters
        assert response.status_code == 302
        redirect_url = response.headers["location"]
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)

        assert "error" in query_params
        assert query_params["error"][0] == "invalid_request"
        # State should be preserved
        assert "state" in query_params
        assert query_params["state"][0] == "test_state"

    async def test_authorize_invalid_scope(
        self, test_client: httpx.AsyncClient, registered_client, pkce_challenge
    ):
        """Test authorization endpoint with invalid scope.

        Invalid scope should redirect with invalid_scope error.
        """

        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered_client["client_id"],
                "redirect_uri": "https://client.example.com/callback",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "scope": "invalid_scope_that_does_not_exist",
                "state": "test_state",
            },
        )

        # Should redirect with error parameters
        assert response.status_code == 302
        redirect_url = response.headers["location"]
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)

        assert "error" in query_params
        assert query_params["error"][0] == "invalid_scope"
        # State should be preserved
        assert "state" in query_params
        assert query_params["state"][0] == "test_state"
