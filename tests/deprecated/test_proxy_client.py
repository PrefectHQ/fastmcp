"""Tests for deprecated FastMCPProxy client parameter."""

import warnings

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.proxy import FastMCPProxy, ProxyClient


@pytest.fixture
def simple_server():
    """Create a simple FastMCP server for testing."""
    server = FastMCP("TestServer")

    @server.tool
    def simple_tool() -> str:
        return "test_result"

    return server


class TestDeprecatedClientParameter:
    """Test the deprecated client parameter in FastMCPProxy."""

    def test_client_parameter_deprecation_warning(self, simple_server):
        """Test that using the client parameter raises a deprecation warning."""
        client = Client(simple_server)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")  # Ensure all warnings are captured

            FastMCPProxy(client=client)

            # Verify a deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "client' to FastMCPProxy is deprecated" in str(w[0].message)
            assert "client_factory" in str(w[0].message)

    def test_client_parameter_still_works(self, simple_server):
        """Test that the deprecated client parameter still functions."""
        client = ProxyClient(simple_server)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Suppress warnings for functionality test

            proxy = FastMCPProxy(client=client)

            # Verify the proxy was created successfully
            assert proxy is not None
            assert hasattr(proxy, "client_factory")
            assert callable(proxy.client_factory)

            # Verify the factory returns a new client instance (session isolation for backwards compatibility)
            returned_client = proxy.client_factory()
            assert returned_client is not client
            assert isinstance(returned_client, type(client))

    def test_cannot_specify_both_client_and_factory(self, simple_server):
        """Test that specifying both client and client_factory raises an error."""
        client = Client(simple_server)

        def factory():
            return Client(simple_server)

        with pytest.raises(
            ValueError, match="Cannot specify both 'client' and 'client_factory'"
        ):
            FastMCPProxy(client=client, client_factory=factory)

    def test_must_specify_client_factory_when_no_client(self):
        """Test that client_factory is required when client is not provided."""
        with pytest.raises(ValueError, match="Must specify 'client_factory'"):
            FastMCPProxy()

    def test_client_factory_preferred_over_deprecated_client(self, simple_server):
        """Test that the recommended client_factory approach works without warnings."""

        def factory():
            return ProxyClient(simple_server)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            proxy = FastMCPProxy(client_factory=factory)

            # Verify no warnings were raised
            assert len(w) == 0

            # Verify the proxy works correctly
            assert proxy is not None
            assert proxy.client_factory is factory

    async def test_deprecated_client_functional_test(self, simple_server):
        """End-to-end test that deprecated client parameter still works functionally."""
        client = ProxyClient(simple_server)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            proxy = FastMCPProxy(client=client)

        # Test that the proxy can actually handle requests
        async with Client(proxy) as proxy_client:
            result = await proxy_client.call_tool("simple_tool", {})
            assert result.data == "test_result"
