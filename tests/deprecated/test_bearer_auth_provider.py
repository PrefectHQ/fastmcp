import pytest

# reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


def test_bearer_auth_provider_deprecated():
    """Test that BearerAuthProvider import shows deprecation warning."""
    with pytest.warns(
        DeprecationWarning,
        match="The `fastmcp.server.auth.providers.bearer` module is deprecated and will be removed in a future version. Please use `fastmcp.server.auth.providers.jwt.JWTVerifier` instead of this module's BearerAuthProvider.",
    ):
        from fastmcp.server.auth import BearerAuthProvider  # noqa: F401
