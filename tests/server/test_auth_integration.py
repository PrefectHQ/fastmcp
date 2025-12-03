import base64
import hashlib
import secrets
import time
import unittest.mock
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

        # Check if token is expired
        # if token_info.expires_at < int(time.time()):
        #     raise InvalidTokenError("Access token has expired")

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


@pytest.fixture
async def auth_code(test_client, registered_client, pkce_challenge, request):
    """Get an authorization code.

    Parameters can be customized via indirect parameterization:
    @pytest.mark.parametrize("auth_code",
                            [{"redirect_uri": "https://client.example.com/other-callback"}],
                            indirect=True)
    """
    # Default authorize params
    auth_params = {
        "response_type": "code",
        "client_id": registered_client["client_id"],
        "redirect_uri": "https://client.example.com/callback",
        "code_challenge": pkce_challenge["code_challenge"],
        "code_challenge_method": "S256",
        "state": "test_state",
    }

    # Override with any parameters from the test
    if hasattr(request, "param") and request.param:
        auth_params.update(request.param)

    response = await test_client.get("/authorize", params=auth_params)
    assert response.status_code == 302, f"Failed to get auth code: {response.content}"

    # Extract the authorization code
    redirect_url = response.headers["location"]
    parsed_url = urlparse(redirect_url)
    query_params = parse_qs(parsed_url.query)

    assert "code" in query_params, f"No code in response: {query_params}"
    auth_code = query_params["code"][0]

    return {
        "code": auth_code,
        "redirect_uri": auth_params["redirect_uri"],
        "state": query_params.get("state", [None])[0],
    }


@pytest.fixture
async def tokens(test_client, registered_client, auth_code, pkce_challenge, request):
    """Exchange authorization code for tokens.

    Parameters can be customized via indirect parameterization:
    @pytest.mark.parametrize("tokens",
                            [{"code_verifier": "wrong_verifier"}],
                            indirect=True)
    """
    # Default token request params
    token_params = {
        "grant_type": "authorization_code",
        "client_id": registered_client["client_id"],
        "client_secret": registered_client["client_secret"],
        "code": auth_code["code"],
        "code_verifier": pkce_challenge["code_verifier"],
        "redirect_uri": auth_code["redirect_uri"],
    }

    # Override with any parameters from the test
    if hasattr(request, "param") and request.param:
        token_params.update(request.param)

    response = await test_client.post("/token", data=token_params)

    # Don't assert success here since some tests will intentionally cause errors
    return {
        "response": response,
        "params": token_params,
    }


class TestAuthEndpoints:
    async def test_metadata_endpoint(self, test_client: httpx.AsyncClient):
        """Test the OAuth 2.1 metadata endpoint."""
        print("Sending request to metadata endpoint")
        response = await test_client.get("/.well-known/oauth-authorization-server")
        print(f"Got response: {response.status_code}")
        if response.status_code != 200:
            print(f"Response content: {response.content}")
        assert response.status_code == 200

        metadata = response.json()
        assert metadata["issuer"] == "https://auth.example.com/"
        assert (
            metadata["authorization_endpoint"] == "https://auth.example.com/authorize"
        )
        assert metadata["token_endpoint"] == "https://auth.example.com/token"
        assert metadata["registration_endpoint"] == "https://auth.example.com/register"
        assert metadata["revocation_endpoint"] == "https://auth.example.com/revoke"
        assert metadata["response_types_supported"] == ["code"]
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        assert set(metadata["token_endpoint_auth_methods_supported"]) == {
            "client_secret_post",
            "client_secret_basic",
        }
        assert metadata["grant_types_supported"] == [
            "authorization_code",
            "refresh_token",
        ]
        assert metadata["service_documentation"] == "https://docs.example.com/"

    async def test_token_validation_error(self, test_client: httpx.AsyncClient):
        """Test token endpoint error - missing client_id returns auth error."""
        # Missing required fields - SDK validates client_id first
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                # Missing code, code_verifier, client_id, etc.
            },
        )
        error_response = response.json()
        # SDK validates client_id before other fields, returning unauthorized_client
        # (FastMCP's OAuthProxy transforms this to invalid_client, but this test
        # uses the SDK's create_auth_routes directly)
        assert error_response["error"] == "unauthorized_client"
        assert "error_description" in error_response

    async def test_token_invalid_auth_code(
        self, test_client, registered_client, pkce_challenge
    ):
        """Test token endpoint error - authorization code does not exist."""
        # Try to use a non-existent authorization code
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "code": "non_existent_auth_code",
                "code_verifier": pkce_challenge["code_verifier"],
                "redirect_uri": "https://client.example.com/callback",
            },
        )
        print(f"Status code: {response.status_code}")
        print(f"Response body: {response.content}")
        print(f"Response JSON: {response.json()}")
        assert response.status_code == 400
        error_response = response.json()
        assert error_response["error"] == "invalid_grant"
        assert (
            "authorization code does not exist" in error_response["error_description"]
        )

    async def test_token_expired_auth_code(
        self,
        test_client,
        registered_client,
        auth_code,
        pkce_challenge,
        mock_oauth_provider,
    ):
        """Test token endpoint error - authorization code has expired."""
        # Get the current time for our time mocking
        current_time = time.time()

        # Find the auth code object
        code_value = auth_code["code"]
        found_code = None
        for code_obj in mock_oauth_provider.auth_codes.values():
            if code_obj.code == code_value:
                found_code = code_obj
                break

        assert found_code is not None

        # Authorization codes are typically short-lived (5 minutes = 300 seconds)
        # So we'll mock time to be 10 minutes (600 seconds) in the future
        with unittest.mock.patch("time.time", return_value=current_time + 600):
            # Try to use the expired authorization code
            response = await test_client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": registered_client["client_id"],
                    "client_secret": registered_client["client_secret"],
                    "code": code_value,
                    "code_verifier": pkce_challenge["code_verifier"],
                    "redirect_uri": auth_code["redirect_uri"],
                },
            )
            assert response.status_code == 400
            error_response = response.json()
            assert error_response["error"] == "invalid_grant"
            assert (
                "authorization code has expired" in error_response["error_description"]
            )

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
    async def test_token_redirect_uri_mismatch(
        self, test_client, registered_client, auth_code, pkce_challenge
    ):
        """Test token endpoint error - redirect URI mismatch."""
        # Try to use the code with a different redirect URI
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "code": auth_code["code"],
                "code_verifier": pkce_challenge["code_verifier"],
                # Different from the one used in /authorize
                "redirect_uri": "https://client.example.com/other-callback",
            },
        )
        assert response.status_code == 400
        error_response = response.json()
        assert error_response["error"] == "invalid_request"
        assert "redirect_uri did not match" in error_response["error_description"]

    async def test_token_code_verifier_mismatch(
        self, test_client, registered_client, auth_code
    ):
        """Test token endpoint error - PKCE code verifier mismatch."""
        # Try to use the code with an incorrect code verifier
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "code": auth_code["code"],
                # Different from the one used to create challenge
                "code_verifier": "incorrect_code_verifier",
                "redirect_uri": auth_code["redirect_uri"],
            },
        )
        assert response.status_code == 400
        error_response = response.json()
        assert error_response["error"] == "invalid_grant"
        assert "incorrect code_verifier" in error_response["error_description"]

    async def test_token_invalid_refresh_token(self, test_client, registered_client):
        """Test token endpoint error - refresh token does not exist."""
        # Try to use a non-existent refresh token
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "refresh_token": "non_existent_refresh_token",
            },
        )
        assert response.status_code == 400
        error_response = response.json()
        assert error_response["error"] == "invalid_grant"
        assert "refresh token does not exist" in error_response["error_description"]

    async def test_token_expired_refresh_token(
        self,
        test_client,
        registered_client,
        auth_code,
        pkce_challenge,
        mock_oauth_provider,
    ):
        """Test token endpoint error - refresh token has expired."""
        # Step 1: First, let's create a token and refresh token at the current time
        current_time = time.time()

        # Exchange authorization code for tokens normally
        token_response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "code": auth_code["code"],
                "code_verifier": pkce_challenge["code_verifier"],
                "redirect_uri": auth_code["redirect_uri"],
            },
        )
        assert token_response.status_code == 200
        tokens = token_response.json()
        refresh_token = tokens["refresh_token"]

        # Step 2: Time travel forward 4 hours (tokens expire in 1 hour by default)
        # Mock the time.time() function to return a value 4 hours in the future
        with unittest.mock.patch(
            "time.time", return_value=current_time + 14400
        ):  # 4 hours = 14400 seconds
            # Try to use the refresh token which should now be considered expired
            response = await test_client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": registered_client["client_id"],
                    "client_secret": registered_client["client_secret"],
                    "refresh_token": refresh_token,
                },
            )

            # In the "future", the token should be considered expired
            assert response.status_code == 400
            error_response = response.json()
            assert error_response["error"] == "invalid_grant"
            assert "refresh token has expired" in error_response["error_description"]

    async def test_token_invalid_scope(
        self, test_client, registered_client, auth_code, pkce_challenge
    ):
        """Test token endpoint error - invalid scope in refresh token request."""
        # Exchange authorization code for tokens
        token_response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "code": auth_code["code"],
                "code_verifier": pkce_challenge["code_verifier"],
                "redirect_uri": auth_code["redirect_uri"],
            },
        )
        assert token_response.status_code == 200

        tokens = token_response.json()
        refresh_token = tokens["refresh_token"]

        # Try to use refresh token with an invalid scope
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "refresh_token": refresh_token,
                "scope": "read write invalid_scope",  # Adding an invalid scope
            },
        )
        assert response.status_code == 400
        error_response = response.json()
        assert error_response["error"] == "invalid_scope"
        assert "cannot request scope" in error_response["error_description"]

    async def test_client_registration(
        self, test_client: httpx.AsyncClient, mock_oauth_provider: MockOAuthProvider
    ):
        """Test client registration."""
        client_metadata = {
            "redirect_uris": ["https://client.example.com/callback"],
            "client_name": "Test Client",
            "client_uri": "https://client.example.com",
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 201, response.content

        client_info = response.json()
        assert "client_id" in client_info
        assert "client_secret" in client_info
        assert client_info["client_name"] == "Test Client"
        assert client_info["redirect_uris"] == ["https://client.example.com/callback"]

        # Verify that the client was registered
        # assert await mock_oauth_provider.clients_store.get_client(
        #     client_info["client_id"]
        # ) is not None

    async def test_client_registration_missing_required_fields(
        self, test_client: httpx.AsyncClient
    ):
        """Test client registration with missing required fields."""
        # Missing redirect_uris which is a required field
        client_metadata = {
            "client_name": "Test Client",
            "client_uri": "https://client.example.com",
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 400
        error_data = response.json()
        assert "error" in error_data
        assert error_data["error"] == "invalid_client_metadata"
        assert error_data["error_description"] == "redirect_uris: Field required"

    async def test_client_registration_invalid_uri(
        self, test_client: httpx.AsyncClient
    ):
        """Test client registration with invalid URIs."""
        # Invalid redirect_uri format
        client_metadata = {
            "redirect_uris": ["not-a-valid-uri"],
            "client_name": "Test Client",
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 400
        error_data = response.json()
        assert "error" in error_data
        assert error_data["error"] == "invalid_client_metadata"
        assert error_data["error_description"] == (
            "redirect_uris.0: Input should be a valid URL, relative URL without a base"
        )

    async def test_client_registration_empty_redirect_uris(
        self, test_client: httpx.AsyncClient
    ):
        """Test client registration with empty redirect_uris array."""
        client_metadata = {
            "redirect_uris": [],  # Empty array
            "client_name": "Test Client",
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 400
        error_data = response.json()
        assert "error" in error_data
        assert error_data["error"] == "invalid_client_metadata"
        assert (
            error_data["error_description"]
            == "redirect_uris: List should have at least 1 item after validation, not 0"
        )

    async def test_authorize_form_post(
        self,
        test_client: httpx.AsyncClient,
        mock_oauth_provider: MockOAuthProvider,
        pkce_challenge,
    ):
        """Test the authorization endpoint using POST with form-encoded data."""
        # Register a client
        client_metadata = {
            "redirect_uris": ["https://client.example.com/callback"],
            "client_name": "Test Client",
            "grant_types": ["authorization_code", "refresh_token"],
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 201
        client_info = response.json()

        # Use POST with form-encoded data for authorization
        response = await test_client.post(
            "/authorize",
            data={
                "response_type": "code",
                "client_id": client_info["client_id"],
                "redirect_uri": "https://client.example.com/callback",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_form_state",
            },
        )
        assert response.status_code == 302

        # Extract the authorization code from the redirect URL
        redirect_url = response.headers["location"]
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)

        assert "code" in query_params
        assert query_params["state"][0] == "test_form_state"

    async def test_authorization_get(
        self,
        test_client: httpx.AsyncClient,
        mock_oauth_provider: MockOAuthProvider,
        pkce_challenge,
    ):
        """Test the full authorization flow."""
        # 1. Register a client
        client_metadata = {
            "redirect_uris": ["https://client.example.com/callback"],
            "client_name": "Test Client",
            "grant_types": ["authorization_code", "refresh_token"],
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 201
        client_info = response.json()

        # 2. Request authorization using GET with query params
        response = await test_client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_info["client_id"],
                "redirect_uri": "https://client.example.com/callback",
                "code_challenge": pkce_challenge["code_challenge"],
                "code_challenge_method": "S256",
                "state": "test_state",
            },
        )
        assert response.status_code == 302

        # 3. Extract the authorization code from the redirect URL
        redirect_url = response.headers["location"]
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)

        assert "code" in query_params
        assert query_params["state"][0] == "test_state"
        auth_code = query_params["code"][0]

        # 4. Exchange the authorization code for tokens
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_info["client_id"],
                "client_secret": client_info["client_secret"],
                "code": auth_code,
                "code_verifier": pkce_challenge["code_verifier"],
                "redirect_uri": "https://client.example.com/callback",
            },
        )
        assert response.status_code == 200

        token_response = response.json()
        assert "access_token" in token_response
        assert "token_type" in token_response
        assert "refresh_token" in token_response
        assert "expires_in" in token_response
        assert token_response["token_type"] == "Bearer"

        # 5. Verify the access token
        access_token = token_response["access_token"]
        refresh_token = token_response["refresh_token"]

        # Create a test client with the token
        auth_info = await mock_oauth_provider.load_access_token(access_token)
        assert auth_info
        assert auth_info.client_id == client_info["client_id"]
        assert "read" in auth_info.scopes
        assert "write" in auth_info.scopes

        # 6. Refresh the token
        response = await test_client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_info["client_id"],
                "client_secret": client_info["client_secret"],
                "refresh_token": refresh_token,
                "redirect_uri": "https://client.example.com/callback",
            },
        )
        assert response.status_code == 200

        new_token_response = response.json()
        assert "access_token" in new_token_response
        assert "refresh_token" in new_token_response
        assert new_token_response["access_token"] != access_token
        assert new_token_response["refresh_token"] != refresh_token

        # 7. Revoke the token
        response = await test_client.post(
            "/revoke",
            data={
                "client_id": client_info["client_id"],
                "client_secret": client_info["client_secret"],
                "token": new_token_response["access_token"],
            },
        )
        assert response.status_code == 200

        # Verify that the token was revoked
        assert (
            await mock_oauth_provider.load_access_token(
                new_token_response["access_token"]
            )
            is None
        )

    async def test_revoke_invalid_token(self, test_client, registered_client):
        """Test revoking an invalid token."""
        response = await test_client.post(
            "/revoke",
            data={
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "token": "invalid_token",
            },
        )
        # per RFC, this should return 200 even if the token is invalid
        assert response.status_code == 200

    async def test_revoke_with_malformed_token(self, test_client, registered_client):
        response = await test_client.post(
            "/revoke",
            data={
                "client_id": registered_client["client_id"],
                "client_secret": registered_client["client_secret"],
                "token": 123,
                "token_type_hint": "asdf",
            },
        )
        assert response.status_code == 400
        error_response = response.json()
        assert error_response["error"] == "invalid_request"
        assert "token_type_hint" in error_response["error_description"]

    async def test_client_registration_disallowed_scopes(
        self, test_client: httpx.AsyncClient
    ):
        """Test client registration with scopes that are not allowed."""
        client_metadata = {
            "redirect_uris": ["https://client.example.com/callback"],
            "client_name": "Test Client",
            "scope": "read write profile admin",  # 'admin' is not in valid_scopes
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 400
        error_data = response.json()
        assert "error" in error_data
        assert error_data["error"] == "invalid_client_metadata"
        assert "scope" in error_data["error_description"]
        assert "admin" in error_data["error_description"]

    async def test_client_registration_default_scopes(
        self, test_client: httpx.AsyncClient, mock_oauth_provider: MockOAuthProvider
    ):
        client_metadata = {
            "redirect_uris": ["https://client.example.com/callback"],
            "client_name": "Test Client",
            # No scope specified
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 201
        client_info = response.json()

        # Verify client was registered successfully
        assert client_info["scope"] == "read write"

        # Retrieve the client from the store to verify default scopes
        registered_client = await mock_oauth_provider.get_client(
            client_info["client_id"]
        )
        assert registered_client is not None

        # Check that default scopes were applied
        assert registered_client.scope == "read write"

    async def test_client_registration_invalid_grant_type(
        self, test_client: httpx.AsyncClient
    ):
        client_metadata = {
            "redirect_uris": ["https://client.example.com/callback"],
            "client_name": "Test Client",
            "grant_types": ["authorization_code"],
        }

        response = await test_client.post(
            "/register",
            json=client_metadata,
        )
        assert response.status_code == 400
        error_data = response.json()
        assert "error" in error_data
        assert error_data["error"] == "invalid_client_metadata"
        assert (
            error_data["error_description"]
            == "grant_types must be authorization_code and refresh_token"
        )


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
