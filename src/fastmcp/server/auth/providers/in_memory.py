import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthToken,
)
from pydantic import AnyHttpUrl

from fastmcp.server.auth.auth import (
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)

# Default expiration times (in seconds)
DEFAULT_AUTH_CODE_EXPIRY_SECONDS = 5 * 60  # 5 minutes
DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS = 60 * 60  # 1 hour
DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS = None  # No expiry


class InMemoryOAuthProvider(OAuthProvider):
    """
    An in-memory OAuth provider for testing purposes.
    It simulates the OAuth 2.1 flow locally without external calls.
    """

    def __init__(
        self,
        base_url: AnyHttpUrl | str | None = None,
        service_documentation_url: AnyHttpUrl | str | None = None,
        client_registration_options: ClientRegistrationOptions | None = None,
        revocation_options: RevocationOptions | None = None,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(
            base_url=base_url or "http://fastmcp.example.com",
            service_documentation_url=service_documentation_url,
            client_registration_options=client_registration_options,
            revocation_options=revocation_options,
            required_scopes=required_scopes,
        )
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

        # For revoking associated tokens
        self._access_to_refresh_map: dict[
            str, str
        ] = {}  # access_token_str -> refresh_token_str
        self._refresh_to_access_map: dict[
            str, str
        ] = {}  # refresh_token_str -> access_token_str

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id in self.clients:
            # As per RFC 7591, if client_id is already known, it's an update.
            # For this simple provider, we'll treat it as re-registration.
            # A real provider might handle updates or raise errors for conflicts.
            pass
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """
        Simulates user authorization and generates an authorization code.
        Returns a redirect URI with the code and state.
        """
        if client.client_id not in self.clients:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description=f"Client '{client.client_id}' not registered.",
            )

        # Validate redirect_uri (already validated by AuthorizationHandler, but good practice)
        try:
            # OAuthClientInformationFull should have a method like validate_redirect_uri
            # For this test provider, we assume it's valid if it matches one in client_info
            # The AuthorizationHandler already does robust validation using client.validate_redirect_uri
            if params.redirect_uri not in client.redirect_uris:
                # This check might be too simplistic if redirect_uris can be patterns
                # or if params.redirect_uri is None and client has a default.
                # However, the AuthorizationHandler handles the primary validation.
                pass  # Let's assume AuthorizationHandler did its job.
        except Exception:  # Replace with specific validation error if client.validate_redirect_uri existed
            raise AuthorizeError(
                error="invalid_request", error_description="Invalid redirect_uri."
            )

        auth_code_value = f"test_auth_code_{secrets.token_hex(16)}"
        expires_at = time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS

        # Ensure scopes are a list
        scopes_list = params.scopes if params.scopes is not None else []
        if client.scope:  # Filter params.scopes against client's registered scopes
            client_allowed_scopes = set(client.scope.split())
            scopes_list = [s for s in scopes_list if s in client_allowed_scopes]

        auth_code = AuthorizationCode(
            code=auth_code_value,
            client_id=client.client_id,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=scopes_list,
            expires_at=expires_at,
            code_challenge=params.code_challenge,
            # code_challenge_method is assumed S256 by the framework
        )
        self.auth_codes[auth_code_value] = auth_code

        return construct_redirect_uri(
            str(params.redirect_uri), code=auth_code_value, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        auth_code_obj = self.auth_codes.get(authorization_code)
        if auth_code_obj:
            if auth_code_obj.client_id != client.client_id:
                return None  # Belongs to a different client
            if auth_code_obj.expires_at < time.time():
                del self.auth_codes[authorization_code]  # Expired
                return None
            return auth_code_obj
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Authorization code should have been validated (existence, expiry, client_id match)
        # by the TokenHandler calling load_authorization_code before this.
        # We might want to re-verify or simply trust it's valid.

        if authorization_code.code not in self.auth_codes:
            raise TokenError(
                "invalid_grant", "Authorization code not found or already used."
            )

        # Consume the auth code
        del self.auth_codes[authorization_code.code]

        access_token_value = f"test_access_token_{secrets.token_hex(32)}"
        refresh_token_value = f"test_refresh_token_{secrets.token_hex(32)}"

        access_token_expires_at = int(time.time() + DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)

        # Refresh token expiry
        refresh_token_expires_at = None
        if DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS is not None:
            refresh_token_expires_at = int(
                time.time() + DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS
            )

        self.access_tokens[access_token_value] = AccessToken(
            token=access_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=access_token_expires_at,
        )
        self.refresh_tokens[refresh_token_value] = RefreshToken(
            token=refresh_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,  # Refresh token inherits scopes
            expires_at=refresh_token_expires_at,
        )

        self._access_to_refresh_map[access_token_value] = refresh_token_value
        self._refresh_to_access_map[refresh_token_value] = access_token_value

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
            refresh_token=refresh_token_value,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token_obj = self.refresh_tokens.get(refresh_token)
        if token_obj:
            if token_obj.client_id != client.client_id:
                return None  # Belongs to different client
            if token_obj.expires_at is not None and token_obj.expires_at < time.time():
                self._revoke_internal(
                    refresh_token_str=token_obj.token
                )  # Clean up expired
                return None
            return token_obj
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,  # This is the RefreshToken object, already loaded
        scopes: list[str],  # Requested scopes for the new access token
    ) -> OAuthToken:
        # Validate scopes: requested scopes must be a subset of original scopes
        original_scopes = set(refresh_token.scopes)
        requested_scopes = set(scopes)
        if not requested_scopes.issubset(original_scopes):
            raise TokenError(
                "invalid_scope",
                "Requested scopes exceed those authorized by the refresh token.",
            )

        # Invalidate old refresh token and its associated access token (rotation)
        self._revoke_internal(refresh_token_str=refresh_token.token)

        # Issue new tokens
        new_access_token_value = f"test_access_token_{secrets.token_hex(32)}"
        new_refresh_token_value = f"test_refresh_token_{secrets.token_hex(32)}"

        access_token_expires_at = int(time.time() + DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS)

        # Refresh token expiry
        refresh_token_expires_at = None
        if DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS is not None:
            refresh_token_expires_at = int(
                time.time() + DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS
            )

        self.access_tokens[new_access_token_value] = AccessToken(
            token=new_access_token_value,
            client_id=client.client_id,
            scopes=scopes,  # Use newly requested (and validated) scopes
            expires_at=access_token_expires_at,
        )
        self.refresh_tokens[new_refresh_token_value] = RefreshToken(
            token=new_refresh_token_value,
            client_id=client.client_id,
            scopes=scopes,  # New refresh token also gets these scopes
            expires_at=refresh_token_expires_at,
        )

        self._access_to_refresh_map[new_access_token_value] = new_refresh_token_value
        self._refresh_to_access_map[new_refresh_token_value] = new_access_token_value

        return OAuthToken(
            access_token=new_access_token_value,
            token_type="Bearer",
            expires_in=DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
            refresh_token=new_refresh_token_value,
            scope=" ".join(scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        token_obj = self.access_tokens.get(token)
        if token_obj:
            if token_obj.expires_at is not None and token_obj.expires_at < time.time():
                self._revoke_internal(
                    access_token_str=token_obj.token
                )  # Clean up expired
                return None
            return token_obj
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        """
        Verify a bearer token and return access info if valid.

        This method implements the TokenVerifier protocol by delegating
        to our existing load_access_token method.

        Args:
            token: The token string to validate

        Returns:
            AccessToken object if valid, None if invalid or expired
        """
        return await self.load_access_token(token)

    def _revoke_internal(
        self, access_token_str: str | None = None, refresh_token_str: str | None = None
    ):
        """Internal helper to remove tokens and their associations."""
        removed_access_token = None
        removed_refresh_token = None

        if access_token_str:
            if access_token_str in self.access_tokens:
                del self.access_tokens[access_token_str]
                removed_access_token = access_token_str

            # Get associated refresh token
            associated_refresh = self._access_to_refresh_map.pop(access_token_str, None)
            if associated_refresh:
                if associated_refresh in self.refresh_tokens:
                    del self.refresh_tokens[associated_refresh]
                    removed_refresh_token = associated_refresh
                self._refresh_to_access_map.pop(associated_refresh, None)

        if refresh_token_str:
            if refresh_token_str in self.refresh_tokens:
                del self.refresh_tokens[refresh_token_str]
                removed_refresh_token = refresh_token_str

            # Get associated access token
            associated_access = self._refresh_to_access_map.pop(refresh_token_str, None)
            if associated_access:
                if associated_access in self.access_tokens:
                    del self.access_tokens[associated_access]
                    removed_access_token = associated_access
                self._access_to_refresh_map.pop(associated_access, None)

        # Clean up any dangling references if one part of the pair was already gone
        if removed_access_token and removed_access_token in self._access_to_refresh_map:
            del self._access_to_refresh_map[removed_access_token]
        if (
            removed_refresh_token
            and removed_refresh_token in self._refresh_to_access_map
        ):
            del self._refresh_to_access_map[removed_refresh_token]

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        """Revokes an access or refresh token and its counterpart."""
        if isinstance(token, AccessToken):
            self._revoke_internal(access_token_str=token.token)
        elif isinstance(token, RefreshToken):
            self._revoke_internal(refresh_token_str=token.token)
        # If token is not found or already revoked, _revoke_internal does nothing, which is correct.
