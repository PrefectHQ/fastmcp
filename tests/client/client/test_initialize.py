"""Client initialization tests."""

from fastmcp.client import Client
from fastmcp.server.server import FastMCP


class TestInitialize:
    """Tests for client initialization behavior."""

    async def test_auto_initialize_default(self, fastmcp_server):
        """Test that auto_initialize=True is the default and works automatically."""
        client = Client(fastmcp_server)

        async with client:
            # Should be automatically initialized
            assert client.initialize_result is not None
            assert client.initialize_result.serverInfo.name == "TestServer"
            assert client.initialize_result.instructions is None

    async def test_auto_initialize_explicit_true(self, fastmcp_server):
        """Test explicit auto_initialize=True."""
        client = Client(fastmcp_server, auto_initialize=True)

        async with client:
            assert client.initialize_result is not None
            assert client.initialize_result.serverInfo.name == "TestServer"

    async def test_auto_initialize_false(self, fastmcp_server):
        """Test that auto_initialize=False prevents automatic initialization."""
        client = Client(fastmcp_server, auto_initialize=False)

        async with client:
            # Should not be automatically initialized
            assert client.initialize_result is None

    async def test_manual_initialize(self, fastmcp_server):
        """Test manual initialization when auto_initialize=False."""
        client = Client(fastmcp_server, auto_initialize=False)

        async with client:
            # Manually initialize
            result = await client.initialize()

            assert result is not None
            assert result.serverInfo.name == "TestServer"
            assert client.initialize_result is result

    async def test_initialize_idempotent(self, fastmcp_server):
        """Test that calling initialize() multiple times returns cached result."""
        client = Client(fastmcp_server, auto_initialize=False)

        async with client:
            result1 = await client.initialize()
            result2 = await client.initialize()
            result3 = await client.initialize()

            # All should return the same cached result
            assert result1 is result2
            assert result2 is result3

    async def test_initialize_with_instructions(self):
        """Test that server instructions are available via initialize_result."""
        server = FastMCP("InstructionsServer", instructions="Use the greet tool!")

        @server.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        client = Client(server)

        async with client:
            result = client.initialize_result
            assert result is not None
            assert result.instructions == "Use the greet tool!"

    async def test_initialize_timeout_custom(self, fastmcp_server):
        """Test custom timeout for initialize()."""
        client = Client(fastmcp_server, auto_initialize=False)

        async with client:
            # Should succeed with reasonable timeout
            result = await client.initialize(timeout=5.0)
            assert result is not None

    async def test_initialize_property_after_auto_init(self, fastmcp_server):
        """Test accessing initialize_result property after auto-initialization."""
        client = Client(fastmcp_server, auto_initialize=True)

        async with client:
            # Access via property
            result = client.initialize_result
            assert result is not None
            assert result.serverInfo.name == "TestServer"

            # Call method - should return cached
            result2 = await client.initialize()
            assert result is result2

    async def test_initialize_property_before_connect(self, fastmcp_server):
        """Test that initialize_result property is None before connection."""
        client = Client(fastmcp_server)

        # Not yet connected
        assert client.initialize_result is None

    async def test_manual_initialize_can_call_tools(self, fastmcp_server):
        """Test that manually initialized client can call tools."""
        client = Client(fastmcp_server, auto_initialize=False)

        async with client:
            await client.initialize()

            # Should be able to call tools after manual initialization
            result = await client.call_tool("greet", {"name": "World"})
            assert "Hello, World!" in str(result.content)
