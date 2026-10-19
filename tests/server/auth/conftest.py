"""Shared fixtures for authentication tests."""

import pytest

from fastmcp.server.auth.oidc_proxy import _clear_oidc_configuration_cache


@pytest.fixture(autouse=True)
def clear_oidc_discovery_cache():
    """Prevent process-local discovery cache state from leaking between tests."""
    _clear_oidc_configuration_cache()
    yield
    _clear_oidc_configuration_cache()
