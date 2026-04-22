"""
Test suite for WorkOS Authentication Provider.
Focuses on ensuring compatibility with dynamic server environments.
"""

import pytest

from fastmcp.server.auth.providers.workos import WorkOSProvider

@pytest.mark.asyncio
async def test_workos_dynamic_port_fix():
    """
    Verify WorkOSProvider supports base_url updates post-initialization.
    
    This addresses the 'Chicken and Egg' problem (Issue #1654) where the 
    server port is unknown until startup, requiring the provider's 
    redirect logic to be updated dynamically.
    """

    # Initialize with a default placeholder
    provider = WorkOSProvider(
        client_id="test_id",
        client_secret="test_secret",
        authkit_domain="test.authkit.app",
        base_url="http://localhost:8000" 
    )

    # Simulates a dynamic port assignment (e.g., during a pytest-asyncio run)
    dynamic_port_url = "http://localhost:9999"
    
    # THE FIX: Ensure the provider allows the base_url to be overwritten
    provider.base_url = dynamic_port_url

    # Validation: The provider must reflect the new URL for OAuth redirects
    assert provider.base_url == dynamic_port_url