"""Debug token verifier for testing and special cases.

This module provides a flexible token verifier that delegates validation
to a custom callable. Useful for testing, development, or scenarios where
standard verification isn't possible (like opaque tokens without introspection).

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.debug import DebugTokenVerifier

    # Accept all tokens (default - useful for testing)
    auth = DebugTokenVerifier()

    # Custom sync validation logic
    auth = DebugTokenVerifier(validate=lambda token: token.startswith("valid-"))

    # Custom async validation logic
    async def check_cache(token: str) -> bool:
        return await redis.exists(f"token:{token}")

    auth = DebugTokenVerifier(validate=check_cache)

    mcp = FastMCP("My Server", auth=auth)
    ```
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class DebugTokenVerifier(TokenVerifier):
    """Token verifier with custom validation logic.

    This verifier delegates token validation to a user-provided callable.
    By default, it accepts all non-empty tokens (useful for testing).

    Use cases:
    - Testing: Accept any token without real verification
    - Development: Custom validation logic for prototyping
    - Opaque tokens: When you have tokens with no introspection endpoint

    WARNING: This bypasses standard security checks. Only use in controlled
    environments or when you understand the security implications.
    """

    def __init__(
        self,
        validate: Callable[[str], bool]
        | Callable[[str], Awaitable[bool]] = lambda token: True,
        client_id: str = "debug-client",
        scopes: list[str] | None = None,
        required_scopes: list[str] | None = None,
    ):
        """Initialize the debug token verifier.

        Args:
            validate: Callable that takes a token string and returns True if valid.
                Can be sync or async. Default accepts all tokens.
            client_id: Client ID to assign to validated tokens
            scopes: Scopes to assign to validated tokens
            required_scopes: Required scopes (inherited from TokenVerifier base class)
        """
        super().__init__(required_scopes=required_scopes)
        self.validate = validate
        self.client_id = client_id
        self.scopes = scopes or []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token using custom validation logic.

        Args:
            token: The token string to validate

        Returns:
            AccessToken if validation succeeds, None otherwise
        """
        # Reject empty tokens
        if not token or not token.strip():
            logger.debug("Rejecting empty token")
            return None

        try:
            # Call validation function and await if result is awaitable
            result = self.validate(token)
            if inspect.isawaitable(result):
                is_valid = await result
            else:
                is_valid = result

            if not is_valid:
                logger.debug("Token validation failed: callable returned False")
                return None

            # Return valid AccessToken
            return AccessToken(
                token=token,
                client_id=self.client_id,
                scopes=self.scopes,
                expires_at=None,  # No expiration
                claims={"token": token},  # Store original token in claims
            )

        except Exception as e:
            logger.debug("Token validation error: %s", e, exc_info=True)
            return None
