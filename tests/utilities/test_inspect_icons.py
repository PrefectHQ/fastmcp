"""Tests for icon extraction and formatting functions in inspect.py."""

import importlib.metadata

from mcp.server.fastmcp import FastMCP as FastMCP1x

import fastmcp
from fastmcp import FastMCP
from fastmcp.utilities.inspect import (
    InspectFormat,
    format_fastmcp_info,
    format_info,
    format_mcp_info,
    inspect_fastmcp,
    inspect_fastmcp_v1,
)


class TestIconExtraction:
    """Tests for icon extraction in inspect."""

    async def test_server_icons_and_website(self):
        """Test that server-level icons and website_url are extracted."""
        from mcp.types import Icon

        mcp = FastMCP(
            "IconServer",
            website_url="https://example.com",
            icons=[
                Icon(
                    src="https://example.com/icon.png",
                    mimeType="image/png",
                    sizes=["48x48"],
                )
            ],
        )

        info = await inspect_fastmcp(mcp)

        assert info.website_url == "https://example.com"
        assert info.icons is not None
        assert len(info.icons) == 1
        assert info.icons[0]["src"] == "https://example.com/icon.png"
        assert info.icons[0]["mimeType"] == "image/png"
        assert info.icons[0]["sizes"] == ["48x48"]

    async def test_server_without_icons(self):
        """Test that servers without icons have None for icons and website_url."""
        mcp = FastMCP("NoIconServer")

        info = await inspect_fastmcp(mcp)

        assert info.website_url is None
        assert info.icons is None

    async def test_tool_icons(self):
        """Test that tool icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("ToolIconServer")

        @mcp.tool(
            icons=[
                Icon(
                    src="https://example.com/calculator.png",
                    mimeType="image/png",
                )
            ]
        )
        def calculate(x: int) -> int:
            """Calculate something."""
            return x * 2

        @mcp.tool
        def no_icon_tool() -> str:
            """Tool without icon."""
            return "no icon"

        info = await inspect_fastmcp(mcp)

        assert len(info.tools) == 2

        # Find the calculate tool
        calculate_tool = next(t for t in info.tools if t.name == "calculate")
        assert calculate_tool.icons is not None
        assert len(calculate_tool.icons) == 1
        assert calculate_tool.icons[0]["src"] == "https://example.com/calculator.png"

        # Find the no_icon tool
        no_icon = next(t for t in info.tools if t.name == "no_icon_tool")
        assert no_icon.icons is None

    async def test_resource_icons(self):
        """Test that resource icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("ResourceIconServer")

        @mcp.resource(
            "resource://data",
            icons=[Icon(src="https://example.com/data.png", mimeType="image/png")],
        )
        def get_data() -> str:
            """Get data."""
            return "data"

        @mcp.resource("resource://no-icon")
        def get_no_icon() -> str:
            """Get data without icon."""
            return "no icon"

        info = await inspect_fastmcp(mcp)

        assert len(info.resources) == 2

        # Find the data resource
        data_resource = next(r for r in info.resources if r.uri == "resource://data")
        assert data_resource.icons is not None
        assert len(data_resource.icons) == 1
        assert data_resource.icons[0]["src"] == "https://example.com/data.png"

        # Find the no-icon resource
        no_icon = next(r for r in info.resources if r.uri == "resource://no-icon")
        assert no_icon.icons is None

    async def test_template_icons(self):
        """Test that resource template icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("TemplateIconServer")

        @mcp.resource(
            "resource://user/{id}",
            icons=[Icon(src="https://example.com/user.png", mimeType="image/png")],
        )
        def get_user(id: str) -> str:
            """Get user by ID."""
            return f"user {id}"

        @mcp.resource("resource://item/{id}")
        def get_item(id: str) -> str:
            """Get item without icon."""
            return f"item {id}"

        info = await inspect_fastmcp(mcp)

        assert len(info.templates) == 2

        # Find the user template
        user_template = next(
            t for t in info.templates if t.uri_template == "resource://user/{id}"
        )
        assert user_template.icons is not None
        assert len(user_template.icons) == 1
        assert user_template.icons[0]["src"] == "https://example.com/user.png"

        # Find the no-icon template
        no_icon = next(
            t for t in info.templates if t.uri_template == "resource://item/{id}"
        )
        assert no_icon.icons is None

    async def test_prompt_icons(self):
        """Test that prompt icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("PromptIconServer")

        @mcp.prompt(
            icons=[Icon(src="https://example.com/analyze.png", mimeType="image/png")]
        )
        def analyze(data: str) -> list:
            """Analyze data."""
            return [{"role": "user", "content": f"Analyze: {data}"}]

        @mcp.prompt
        def no_icon_prompt(text: str) -> list:
            """Prompt without icon."""
            return [{"role": "user", "content": text}]

        info = await inspect_fastmcp(mcp)

        assert len(info.prompts) == 2

        # Find the analyze prompt
        analyze_prompt = next(p for p in info.prompts if p.name == "analyze")
        assert analyze_prompt.icons is not None
        assert len(analyze_prompt.icons) == 1
        assert analyze_prompt.icons[0]["src"] == "https://example.com/analyze.png"

        # Find the no-icon prompt
        no_icon = next(p for p in info.prompts if p.name == "no_icon_prompt")
        assert no_icon.icons is None

    async def test_multiple_icons(self):
        """Test that components with multiple icons extract all of them."""
        from mcp.types import Icon

        mcp = FastMCP(
            "MultiIconServer",
            icons=[
                Icon(
                    src="https://example.com/icon-48.png",
                    mimeType="image/png",
                    sizes=["48x48"],
                ),
                Icon(
                    src="https://example.com/icon-96.png",
                    mimeType="image/png",
                    sizes=["96x96"],
                ),
            ],
        )

        @mcp.tool(
            icons=[
                Icon(src="https://example.com/tool-small.png", sizes=["24x24"]),
                Icon(src="https://example.com/tool-large.png", sizes=["48x48"]),
            ]
        )
        def multi_icon_tool() -> str:
            """Tool with multiple icons."""
            return "multi"

        info = await inspect_fastmcp(mcp)

        # Check server icons
        assert info.icons is not None
        assert len(info.icons) == 2
        assert info.icons[0]["sizes"] == ["48x48"]
        assert info.icons[1]["sizes"] == ["96x96"]

        # Check tool icons
        assert len(info.tools) == 1
        assert info.tools[0].icons is not None
        assert len(info.tools[0].icons) == 2
        assert info.tools[0].icons[0]["sizes"] == ["24x24"]
        assert info.tools[0].icons[1]["sizes"] == ["48x48"]

    async def test_data_uri_icons(self):
        """Test that data URI icons are extracted correctly."""
        from mcp.types import Icon

        data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

        mcp = FastMCP("DataURIServer")

        @mcp.tool(icons=[Icon(src=data_uri, mimeType="image/png")])
        def data_uri_tool() -> str:
            """Tool with data URI icon."""
            return "data"

        info = await inspect_fastmcp(mcp)

        assert len(info.tools) == 1
        assert info.tools[0].icons is not None
        assert info.tools[0].icons[0]["src"] == data_uri
        assert info.tools[0].icons[0]["mimeType"] == "image/png"

    async def test_icons_in_fastmcp_v1(self):
        """Test that icons are extracted from FastMCP 1.x servers."""
        from mcp.types import Icon

        mcp = FastMCP1x("Icon1xServer")

        @mcp.tool(
            icons=[Icon(src="https://example.com/v1-tool.png", mimeType="image/png")]
        )
        def v1_tool() -> str:
            """Tool in v1 server."""
            return "v1"

        info = await inspect_fastmcp_v1(mcp)

        assert len(info.tools) == 1
        # v1 servers should also extract icons if present
        if info.tools[0].icons is not None:
            assert info.tools[0].icons[0]["src"] == "https://example.com/v1-tool.png"

    async def test_icons_in_formatted_output(self):
        """Test that icons appear in formatted JSON output."""
        from mcp.types import Icon

        mcp = FastMCP(
            "FormattedIconServer",
            website_url="https://example.com",
            icons=[Icon(src="https://example.com/server.png", mimeType="image/png")],
        )

        @mcp.tool(
            icons=[Icon(src="https://example.com/tool.png", mimeType="image/png")]
        )
        def icon_tool() -> str:
            """Tool with icon."""
            return "icon"

        info = await inspect_fastmcp(mcp)
        json_bytes = format_fastmcp_info(info)

        import json

        data = json.loads(json_bytes)

        # Check server icons in formatted output
        assert data["server"]["website_url"] == "https://example.com"
        assert data["server"]["icons"] is not None
        assert len(data["server"]["icons"]) == 1
        assert data["server"]["icons"][0]["src"] == "https://example.com/server.png"

        # Check tool icons in formatted output
        assert len(data["tools"]) == 1
        assert data["tools"][0]["icons"] is not None
        assert len(data["tools"][0]["icons"]) == 1
        assert data["tools"][0]["icons"][0]["src"] == "https://example.com/tool.png"

    async def test_icons_always_present_in_json(self):
        """Test that icons and website_url fields are always present in JSON, even when None."""
        mcp = FastMCP("AlwaysPresentServer")

        @mcp.tool
        def no_icon() -> str:
            """Tool without icon."""
            return "none"

        info = await inspect_fastmcp(mcp)
        json_bytes = format_fastmcp_info(info)

        import json

        data = json.loads(json_bytes)

        # Fields should always be present, even when None
        assert "website_url" in data["server"]
        assert "icons" in data["server"]
        assert data["server"]["website_url"] is None
        assert data["server"]["icons"] is None

        assert len(data["tools"]) == 1
        assert "icons" in data["tools"][0]
        assert data["tools"][0]["icons"] is None


class TestFormatFunctions:
    """Tests for the formatting functions."""

    async def test_format_fastmcp_info(self):
        """Test formatting as FastMCP-specific JSON."""
        mcp = FastMCP("TestServer", instructions="Test instructions", version="1.2.3")

        @mcp.tool
        def test_tool(x: int) -> dict:
            """A test tool."""
            return {"result": x * 2}

        info = await inspect_fastmcp(mcp)
        json_bytes = format_fastmcp_info(info)

        # Verify it's valid JSON
        import json

        data = json.loads(json_bytes)

        # Check FastMCP-specific fields are present
        assert "server" in data
        assert data["server"]["name"] == "TestServer"
        assert data["server"]["instructions"] == "Test instructions"
        assert data["server"]["generation"] == 2  # v2 server
        assert data["server"]["version"] == "1.2.3"
        assert "capabilities" in data["server"]

        # Check environment information
        assert "environment" in data
        assert data["environment"]["fastmcp"] == fastmcp.__version__
        assert data["environment"]["mcp"] == importlib.metadata.version("mcp")

        # Check tools
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "test_tool"
        assert "tags" in data["tools"][0]

    async def test_format_mcp_info(self):
        """Test formatting as MCP protocol JSON."""
        mcp = FastMCP("TestServer", instructions="Test instructions", version="2.0.0")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.prompt
        def test_prompt(name: str) -> list:
            """Test prompt."""
            return [{"role": "user", "content": f"Hello {name}"}]

        json_bytes = await format_mcp_info(mcp)

        # Verify it's valid JSON
        import json

        data = json.loads(json_bytes)

        # Check MCP protocol structure with camelCase
        assert "serverInfo" in data
        assert data["serverInfo"]["name"] == "TestServer"

        # Check server version in MCP format
        assert data["serverInfo"]["version"] == "2.0.0"

        # MCP format SHOULD have environment fields
        assert "environment" in data
        assert data["environment"]["fastmcp"] == fastmcp.__version__
        assert data["environment"]["mcp"] == importlib.metadata.version("mcp")
        assert "capabilities" in data

        assert "tools" in data
        assert "prompts" in data
        assert "resources" in data
        assert "resourceTemplates" in data

        # Check tools have MCP format (camelCase fields)
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "add"
        assert "inputSchema" in data["tools"][0]

        # FastMCP-specific fields should not be present
        assert "tags" not in data["tools"][0]
        assert "enabled" not in data["tools"][0]

    async def test_format_info_with_fastmcp_format(self):
        """Test format_info with fastmcp format."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def test() -> str:
            return "test"

        # Test with string format
        json_bytes = await format_info(mcp, "fastmcp")
        import json

        data = json.loads(json_bytes)
        assert data["server"]["name"] == "TestServer"
        assert "tags" in data["tools"][0]  # FastMCP-specific field

        # Test with enum format
        json_bytes = await format_info(mcp, InspectFormat.FASTMCP)
        data = json.loads(json_bytes)
        assert data["server"]["name"] == "TestServer"

    async def test_format_info_with_mcp_format(self):
        """Test format_info with mcp format."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def test() -> str:
            return "test"

        json_bytes = await format_info(mcp, "mcp")

        import json

        data = json.loads(json_bytes)
        assert "serverInfo" in data
        assert "tools" in data
        assert "inputSchema" in data["tools"][0]  # MCP uses camelCase

    async def test_format_info_requires_format(self):
        """Test that format_info requires a format parameter."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def test() -> str:
            return "test"

        # Should work with valid formats
        json_bytes = await format_info(mcp, "fastmcp")
        assert json_bytes

        json_bytes = await format_info(mcp, "mcp")
        assert json_bytes

        # Should fail with invalid format
        import pytest

        with pytest.raises(ValueError, match="not a valid InspectFormat"):
            await format_info(mcp, "invalid")  # type: ignore

    async def test_tool_with_output_schema(self):
        """Test that output_schema is properly extracted and included."""
        mcp = FastMCP("TestServer")

        @mcp.tool(
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                    "message": {"type": "string"},
                },
            }
        )
        def compute(x: int) -> dict:
            """Compute something."""
            return {"result": x * 2, "message": f"Doubled {x}"}

        info = await inspect_fastmcp(mcp)

        # Check output_schema is captured
        assert len(info.tools) == 1
        assert info.tools[0].output_schema is not None
        assert info.tools[0].output_schema["type"] == "object"
        assert "result" in info.tools[0].output_schema["properties"]

        # Verify it's included in FastMCP format
        json_bytes = format_fastmcp_info(info)
        import json

        data = json.loads(json_bytes)
        # Tools are at the top level, not nested
        assert data["tools"][0]["output_schema"]["type"] == "object"
