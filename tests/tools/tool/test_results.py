import json
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field

from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool, ToolResult


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


class TestToolResultIsError:
    """A tool can return an error result (isError) instead of raising."""

    def test_to_mcp_result_sets_iserror_and_preserves_content(self):
        result = ToolResult(
            content="boom", structured_content={"code": 42}, is_error=True
        )
        mcp_result = result.to_mcp_result()
        assert isinstance(mcp_result, CallToolResult)
        assert mcp_result.isError is True
        assert isinstance(mcp_result.content[0], TextContent)
        assert mcp_result.content[0].text == "boom"
        assert mcp_result.structuredContent == {"code": 42}

    def test_default_is_not_error(self):
        result = ToolResult(content="ok")
        assert result.is_error is False

    async def test_returned_error_raises_on_client_by_default(self):
        from fastmcp import FastMCP
        from fastmcp.client import Client
        from fastmcp.exceptions import ToolError

        mcp = FastMCP()

        @mcp.tool
        def failing() -> ToolResult:
            return ToolResult(content="upstream boom", is_error=True)

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("failing", {})

    async def test_returned_error_preserves_content_when_not_raising(self):
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @mcp.tool
        def failing() -> ToolResult:
            return ToolResult(content="upstream boom", is_error=True)

        async with Client(mcp) as client:
            result = await client.call_tool("failing", {}, raise_on_error=False)

        assert result.is_error is True
        assert result.content[0].text == "upstream boom"


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


class TestSerializeByAlias:
    """Tests that a model's serialize_by_alias config is honored at runtime.

    pydantic_core's serialization helpers default by_alias to True, which
    silently ignores serialize_by_alias=False. The serialized result and the
    generated output schema must both reflect the model's configured behavior.
    """

    async def test_serialize_by_alias_false_uses_field_names(self):
        """serialize_by_alias=False emits field names in schema, structured, and text."""

        class Biofile(BaseModel):
            model_config = ConfigDict(serialize_by_alias=False)
            id: str = Field(alias="_id")
            filepath: str

        mcp = FastMCP()

        @mcp.tool
        def get_biofile() -> Annotated[Biofile, Field(description="data")]:
            return Biofile(_id="123", filepath="/p")

        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            result = await client.call_tool("get_biofile", {})

        assert result.structured_content == {"id": "123", "filepath": "/p"}
        assert json.loads(result.content[0].text) == {  # type: ignore[union-attr]
            "id": "123",
            "filepath": "/p",
        }
        assert set(tools["get_biofile"].outputSchema["properties"]) == {  # type: ignore[index]
            "id",
            "filepath",
        }

    async def test_unset_config_preserves_alias_default(self):
        """A model with an alias but no serialize config keeps emitting the alias."""

        class Biofile(BaseModel):
            id: str = Field(alias="_id")
            filepath: str

        mcp = FastMCP()

        @mcp.tool
        def get_biofile() -> Biofile:
            return Biofile(_id="123", filepath="/p")

        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            result = await client.call_tool("get_biofile", {})

        assert result.structured_content == {"_id": "123", "filepath": "/p"}
        assert set(tools["get_biofile"].outputSchema["properties"]) == {  # type: ignore[index]
            "_id",
            "filepath",
        }

    async def test_serialize_by_alias_true_uses_alias(self):
        """serialize_by_alias=True emits aliases, same as the default."""

        class Biofile(BaseModel):
            model_config = ConfigDict(serialize_by_alias=True)
            id: str = Field(alias="_id")

        mcp = FastMCP()

        @mcp.tool
        def get_biofile() -> Biofile:
            return Biofile(_id="123")

        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            result = await client.call_tool("get_biofile", {})

        assert result.structured_content == {"_id": "123"}
        assert set(tools["get_biofile"].outputSchema["properties"]) == {"_id"}  # type: ignore[index]

    async def test_nested_models_respect_config(self):
        """serialize_by_alias=False propagates through nested models."""

        class Inner(BaseModel):
            model_config = ConfigDict(serialize_by_alias=False)
            inner_id: str = Field(alias="_iid")

        class Outer(BaseModel):
            model_config = ConfigDict(serialize_by_alias=False)
            id: str = Field(alias="_id")
            inner: Inner

        mcp = FastMCP()

        @mcp.tool
        def get_outer() -> Outer:
            return Outer(_id="1", inner=Inner(_iid="2"))

        async with Client(mcp) as client:
            result = await client.call_tool("get_outer", {})

        assert result.structured_content == {"id": "1", "inner": {"inner_id": "2"}}

    async def test_annotated_optional_return_stays_consistent(self):
        """Annotated[Model, ...] | None resolves the model inside the union arm.

        Regression: the union arm is a typing.Annotated object, so a naive
        isinstance check skipped the model and the schema fell back to aliases
        while the runtime serialized field names, breaking client validation.
        """

        class Biofile(BaseModel):
            model_config = ConfigDict(serialize_by_alias=False)
            id: str = Field(alias="_id")

        mcp = FastMCP()

        @mcp.tool
        def get_biofile() -> Annotated[Biofile, Field(description="x")] | None:
            return Biofile(_id="1")

        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            # client-side validation of structured content against the schema
            # raises if they disagree
            result = await client.call_tool("get_biofile", {})

        schema_props = set(tools["get_biofile"].outputSchema["properties"])  # type: ignore[index]
        assert schema_props == set(result.structured_content)  # type: ignore[arg-type]
        assert result.structured_content == {"result": {"id": "1"}}

    @pytest.mark.parametrize("serialize_by_alias", [True, False, None])
    async def test_schema_and_structured_content_agree(self, serialize_by_alias):
        """The output schema field names always match the structured content keys."""
        if serialize_by_alias is None:
            config = ConfigDict()
        else:
            config = ConfigDict(serialize_by_alias=serialize_by_alias)

        class Model(BaseModel):
            model_config = config
            id: str = Field(alias="_id")
            name: str

        mcp = FastMCP()

        @mcp.tool
        def get_model() -> Model:
            return Model(_id="1", name="x")

        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            result = await client.call_tool("get_model", {})

        schema_props = set(tools["get_model"].outputSchema["properties"])  # type: ignore[index]
        assert schema_props == set(result.structured_content)  # type: ignore[arg-type]
