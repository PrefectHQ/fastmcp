from dataclasses import dataclass
from typing import Any

import pytest

from fastmcp.tools.tool import Tool, ToolResult


class TestToolResultCasting:
    @pytest.fixture
    async def client(self):
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @mcp.tool
        def test_tool(
            unstructured: str | None = None,
            structured: dict[str, Any] | None = None,
            meta: dict[str, Any] | None = None,
        ):
            return ToolResult(
                content=unstructured,
                structured_content=structured,
                meta=meta,
            )

        async with Client(mcp) as client:
            yield client

    async def test_only_unstructured_content(self, client):
        result = await client.call_tool("test_tool", {"unstructured": "test data"})

        assert result.content[0].type == "text"
        assert result.content[0].text == "test data"
        assert result.structured_content is None
        assert result.meta is None

    async def test_neither_unstructured_or_structured_content(self, client):
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            await client.call_tool("test_tool", {})

    async def test_structured_and_unstructured_content(self, client):
        result = await client.call_tool(
            "test_tool",
            {"unstructured": "test data", "structured": {"data_type": "test"}},
        )

        assert result.content[0].type == "text"
        assert result.content[0].text == "test data"
        assert result.structured_content == {"data_type": "test"}
        assert result.meta is None

    async def test_structured_unstructured_and_meta_content(self, client):
        result = await client.call_tool(
            "test_tool",
            {
                "unstructured": "test data",
                "structured": {"data_type": "test"},
                "meta": {"some": "metadata"},
            },
        )

        assert result.content[0].type == "text"
        assert result.content[0].text == "test data"
        assert result.structured_content == {"data_type": "test"}
        assert result.meta == {"some": "metadata"}


class TestUnionReturnTypes:
    """Tests for tools with union return types."""

    async def test_dataclass_union_string_works(self):
        """Test that union of dataclass and string works correctly."""

        @dataclass
        class Data:
            value: int

        def get_data(return_error: bool) -> Data | str:
            if return_error:
                return "error occurred"
            return Data(value=42)

        tool = Tool.from_function(get_data)

        # Test returning dataclass
        result1 = await tool.run({"return_error": False})
        assert result1.structured_content == {"result": {"value": 42}}

        # Test returning string
        result2 = await tool.run({"return_error": True})
        assert result2.structured_content == {"result": "error occurred"}


class TestSerializationAlias:
    """Tests for Pydantic field serialization alias support in tool output schemas."""

    def test_output_schema_respects_serialization_alias(self):
        """Test that Tool.from_function generates output schema using serialization alias."""
        from typing import Annotated

        from pydantic import AliasChoices, BaseModel, Field

        class Component(BaseModel):
            """Model with multiple validation aliases but specific serialization alias."""

            component_id: str = Field(
                validation_alias=AliasChoices("id", "componentId"),
                serialization_alias="componentId",
                description="The ID of the component",
            )

        async def get_component(
            component_id: str,
        ) -> Annotated[Component, Field(description="The component.")]:
            # API returns data with 'id' field
            api_data = {"id": component_id}
            return Component.model_validate(api_data)

        tool = Tool.from_function(get_component, name="get-component")

        # The output schema should use the serialization alias 'componentId'
        # not the first validation alias 'id'
        assert tool.output_schema is not None

        # Object schemas have properties directly at root (MCP spec compliance)
        # Root-level $refs are resolved to ensure type: object at root
        assert "properties" in tool.output_schema
        assert tool.output_schema.get("type") == "object"

        # Should have 'componentId' not 'id' in properties
        assert "componentId" in tool.output_schema["properties"]
        assert "id" not in tool.output_schema["properties"]

        # Should require 'componentId' not 'id'
        assert "componentId" in tool.output_schema.get("required", [])
        assert "id" not in tool.output_schema.get("required", [])

    async def test_tool_execution_with_serialization_alias(self):
        """Test that tool execution works correctly with serialization aliases."""
        from typing import Annotated

        from pydantic import AliasChoices, BaseModel, Field

        from fastmcp import Client, FastMCP

        class Component(BaseModel):
            """Model with multiple validation aliases but specific serialization alias."""

            component_id: str = Field(
                validation_alias=AliasChoices("id", "componentId"),
                serialization_alias="componentId",
                description="The ID of the component",
            )

        mcp = FastMCP("TestServer")

        @mcp.tool
        async def get_component(
            component_id: str,
        ) -> Annotated[Component, Field(description="The component.")]:
            # API returns data with 'id' field
            api_data = {"id": component_id}
            return Component.model_validate(api_data)

        async with Client(mcp) as client:
            # Execute the tool - this should work without validation errors
            result = await client.call_tool(
                "get_component", {"component_id": "test123"}
            )

            # The result should contain the serialized form with 'componentId'
            assert result.structured_content is not None
            # Object types may be wrapped in "result" or not, depending on schema structure
            if "result" in result.structured_content:
                component_data = result.structured_content["result"]
            else:
                component_data = result.structured_content
            assert component_data["componentId"] == "test123"
            assert "id" not in component_data
