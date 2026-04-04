"""Tests for the auto_catalog feature that appends tool summaries to instructions."""

from fastmcp import Client, FastMCP


class TestAutoCatalog:
    async def test_auto_catalog_disabled_by_default(self):
        """auto_catalog defaults to False and instructions are unchanged."""
        mcp = FastMCP(instructions="Base instructions")

        @mcp.tool(description="Say hello.")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            assert result.instructions == "Base instructions"

    async def test_auto_catalog_appends_tool_summary(self):
        """When enabled, a tool catalog is appended to instructions."""
        mcp = FastMCP(instructions="Base instructions", auto_catalog=True)

        @mcp.tool(description="Say hello to a person.")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        @mcp.tool(description="Add two numbers. Returns their sum.")
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            assert instructions.startswith("Base instructions")
            assert "\n\nAvailable tools:\n" in instructions
            assert "  add: Add two numbers." in instructions
            assert "  greet: Say hello to a person." in instructions

    async def test_auto_catalog_no_base_instructions(self):
        """Catalog works when no base instructions are provided."""
        mcp = FastMCP(auto_catalog=True)

        @mcp.tool(description="Ping the server.")
        def ping() -> str:
            return "pong"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            assert "  ping: Ping the server." in instructions

    async def test_auto_catalog_no_tools(self):
        """Catalog is omitted when there are no tools."""
        mcp = FastMCP(instructions="Base", auto_catalog=True)

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            assert result.instructions == "Base"

    async def test_auto_catalog_truncates_at_budget(self):
        """Long catalogs are truncated to stay within the 2KB budget."""
        mcp = FastMCP(auto_catalog=True)

        # Register many tools with long descriptions to exceed budget
        for i in range(200):
            desc = f"Tool number {i} does something very specific and important."

            def make_fn(idx: int, description: str):
                def fn() -> str:
                    return f"result-{idx}"

                fn.__name__ = f"tool_{idx:03d}"
                fn.__doc__ = description
                return fn

            mcp.add_tool(make_fn(i, desc))

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            # Must stay within 2048 bytes
            assert len(instructions.encode("utf-8")) <= 2048
            # Should end with truncation marker
            assert "  ..." in instructions

    async def test_auto_catalog_tool_without_description(self):
        """Tools without descriptions show just the name."""
        mcp = FastMCP(auto_catalog=True)

        @mcp.tool
        def mystery() -> str:
            return "?"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            assert "  mystery" in instructions

    async def test_auto_catalog_with_provider_tools(self):
        """Tools from additional providers are included in the catalog."""
        from fastmcp.server.providers import LocalProvider

        provider = LocalProvider()

        @provider.tool(description="External tool.")
        def external() -> str:
            return "ext"

        mcp = FastMCP(auto_catalog=True, providers=[provider])

        @mcp.tool(description="Internal tool.")
        def internal() -> str:
            return "int"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            assert "  external: External tool." in instructions
            assert "  internal: Internal tool." in instructions

    async def test_auto_catalog_sorted_alphabetically(self):
        """Tool entries are sorted alphabetically."""
        mcp = FastMCP(auto_catalog=True)

        @mcp.tool(description="Zeta tool.")
        def zeta() -> str:
            return "z"

        @mcp.tool(description="Alpha tool.")
        def alpha() -> str:
            return "a"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            alpha_pos = instructions.index("alpha")
            zeta_pos = instructions.index("zeta")
            assert alpha_pos < zeta_pos

    async def test_auto_catalog_first_sentence_only(self):
        """Only the first sentence of the description is included."""
        mcp = FastMCP(auto_catalog=True)

        @mcp.tool(description="Search the database. Supports full-text and vector search. Returns top 10.")
        def search(query: str) -> str:
            return "results"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            instructions = result.instructions
            assert instructions is not None
            assert "  search: Search the database." in instructions
            assert "Supports full-text" not in instructions
