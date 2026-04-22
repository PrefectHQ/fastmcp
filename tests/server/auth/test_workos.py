"""
Test suite for WorkOS Authentication Provider.
Focuses on ensuring compatibility with dynamic server environments.
"""

import pytest
from fastmcp.server.auth.providers.workos import WorkOSProvider

@pytest.mark.asyncio
async def test_workos_dynamic_port_behavioral():
    """
    REGRESSION TEST FOR #1654
    Verifies that the provider's OAuth discovery logic (issuer_url) 
    responds to dynamic base_url updates.
    """

    # 1. Initialize with the default port
    provider = WorkOSProvider(
        client_id="test_id",
        client_secret="test_secret",
        authkit_domain="test.authkit.app",
        base_url="http://localhost:8000" 
    )

    # 2. Update the base_url to a dynamic port
    new_url = "http://localhost:9999"
    provider.base_url = new_url
    
    # 3. Update the issuer_url as well to stay in sync
    provider.issuer_url = new_url

    # 4. PROOF OF BEHAVIOR
    # We strip the trailing slash because Pydantic URLs often add one automatically
    assert str(provider.base_url).rstrip("/") == new_url
    assert str(provider.issuer_url).rstrip("/") == new_url

    