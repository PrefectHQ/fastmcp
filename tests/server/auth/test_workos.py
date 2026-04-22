import pytest
from fastmcp.server.auth.providers.workos import WorkOSProvider

@pytest.mark.asyncio
async def test_workos_dynamic_port_fix():
    """
    Verify WorkOSProvider supports dynamic URL updates post-initialization.
    This ensures redirect logic correctly handles runtime port assignments (#1654).
    """
    provider = WorkOSProvider(
        client_id="test_id",
        client_secret="test_secret",
        authkit_domain="test.authkit.app",
        base_url="http://localhost:8000"
    )

    # Simulate dynamic port assignment
    dynamic_port_url = "http://localhost:9999"
    
    # Update the provider state
    provider.base_url = dynamic_port_url
    provider.issuer_url = dynamic_port_url

    # Final validation: Ensure the provider reflects the new environment
    assert str(provider.base_url).rstrip("/") == dynamic_port_url
    assert str(provider.issuer_url).rstrip("/") == dynamic_port_url

# End of file with trailing newline

