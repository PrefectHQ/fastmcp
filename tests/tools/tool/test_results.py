import json
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, with_config

from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool, ToolResult
from tests.conftest import user_meta


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
        assert user_meta(result.meta) is None

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
        assert user_meta(result.meta) is None

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
        assert user_meta(result.meta) == {"some": "metadata"}


class TestToolResultIsError:
    """A tool can return an error result (isError) instead of raising."""

    def test_to_mcp_result_sets_iserror_and_preserves_content(self):
        result = ToolResult(
            content="boom", structured_content={"code": 42}, is_error=True
        )
        mcp_result = result.to_mcp_result()
        assert isinstance(mcp_result, CallToolResult)
        assert mcp_result.is_error is True
        assert isinstance(mcp_result.content[0], TextContent)
        assert mcp_result.content[0].text == "boom"
        assert mcp_result.structured_content == {"code": 42}

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

    def test_raw_call_tool_result_is_preserved(self):
        tool = Tool.from_function(lambda: None, name="test_tool")
        raw_result = CallToolResult(
            content=[TextContent(type="text", text="upstream boom")],
            structured_content={"code": 42},
            is_error=True,
            _meta={"source": "upstream"},
        )

        result = tool.convert_result(raw_result)

        assert result.to_mcp_result() is raw_result

    async def test_raw_call_tool_result_preserves_protocol_fields(self):
        mcp = FastMCP()

        raw_result = CallToolResult(
            content=[TextContent(type="text", text="upstream boom")],
            structured_content={"code": 42},
            is_error=True,
            _meta={"source": "upstream"},
        )

        @mcp.tool
        def failing() -> CallToolResult:
            return raw_result

        async with Client(mcp) as client:
            result = await client.call_tool_mcp("failing", {})

        received = result.model_dump(by_alias=True)
        # The SDK stamps `serverInfo` into every 2026-era result's `_meta`
        # (spec #3002). Strip it so the assertion covers the protocol fields
        # the tool itself set, which is what FastMCP is responsible for.
        received["_meta"] = user_meta(received["_meta"])
        assert received == raw_result.model_dump(by_alias=True)


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

            model_config = ConfigDict(serialize_by_alias=True)
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

            model_config = ConfigDict(serialize_by_alias=True)
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
    """Tests that typed results use Pydantic's serialization behavior."""

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
        assert set(tools["get_biofile"].output_schema["properties"]) == {  # type: ignore[index]
            "id",
            "filepath",
        }

    async def test_unset_config_uses_pydantic_default(self):
        """A model with no serialize config uses Pydantic's field-name default."""

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

        assert result.structured_content == {"id": "123", "filepath": "/p"}
        assert set(tools["get_biofile"].output_schema["properties"]) == {  # type: ignore[index]
            "id",
            "filepath",
        }

    async def test_model_in_typed_mapping_respects_config(self):
        """A typed mapping's schema and result use the model's field names."""

        class Biofile(BaseModel):
            model_config = ConfigDict(serialize_by_alias=False)
            id: str = Field(alias="_id")

        mcp = FastMCP()

        @mcp.tool
        def get_biofiles() -> dict[str, Biofile]:
            return {"first": Biofile(_id="1")}

        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            result = await client.call_tool("get_biofiles", {})

        value_schema = tools["get_biofiles"].output_schema["additionalProperties"]  # type: ignore[index]
        assert set(value_schema["properties"]) == {"id"}
        assert result.structured_content == {"first": {"id": "1"}}

    async def test_nested_models_use_their_own_alias_configs(self):
        """Nested models can independently enable and disable aliases."""

        class NamedValue(BaseModel):
            model_config = ConfigDict(serialize_by_alias=False)
            value: str = Field(serialization_alias="namedValue")

        class AliasedValue(BaseModel):
            model_config = ConfigDict(serialize_by_alias=True)
            value: str = Field(serialization_alias="aliasedValue")

        class Output(BaseModel):
            named: NamedValue
            aliased: AliasedValue

        mcp = FastMCP()

        @mcp.tool
        def get_output() -> Output:
            return Output(
                named=NamedValue(value="named"),
                aliased=AliasedValue(value="aliased"),
            )

        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            result = await client.call_tool("get_output", {})

        properties = tools["get_output"].output_schema["properties"]  # type: ignore[index]
        assert set(properties["named"]["properties"]) == {"value"}
        assert set(properties["aliased"]["properties"]) == {"aliasedValue"}
        assert result.structured_content == {
            "named": {"value": "named"},
            "aliased": {"aliasedValue": "aliased"},
        }

    async def test_typed_dataclass_container_uses_declared_adapter(self):
        """A typed container preserves its dataclass's alias configuration."""

        @with_config(ConfigDict(serialize_by_alias=True))
        @dataclass
        class Output:
            value: Annotated[str, Field(serialization_alias="dataValue")]

        mcp = FastMCP()

        @mcp.tool
        def get_output() -> list[Output]:
            return [Output(value="data")]

        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            result = await client.call_tool("get_output", {})

        item_schema = tools["get_output"].output_schema["properties"]["result"][  # type: ignore[index]
            "items"
        ]
        assert set(item_schema["properties"]) == {"dataValue"}
        assert result.structured_content == {"result": [{"dataValue": "data"}]}
        assert json.loads(result.content[0].text) == [{"dataValue": "data"}]  # type: ignore[union-attr]

    async def test_serialize_by_alias_true_uses_alias(self):
        """serialize_by_alias=True emits aliases."""

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
        assert set(tools["get_biofile"].output_schema["properties"]) == {"_id"}  # type: ignore[index]


class TestUnserializableReturnValue:
    """An unserializable return value must not become a silent success (#5030)."""

    def _make_server(self) -> FastMCP:
        import threading

        mcp = FastMCP()

        @mcp.tool
        def with_schema() -> dict:
            return {"host": "db1", "lock": threading.Lock()}

        @mcp.tool
        def without_schema():
            return {"host": "db1", "lock": threading.Lock()}

        return mcp

    async def test_output_schema_raises_tool_error(self):
        """With an output schema, serialization failure is an error result,
        not a success carrying the value's repr with no structured content."""
        async with Client(self._make_server()) as client:
            result = await client.call_tool("with_schema", {}, raise_on_error=False)

        assert result.is_error
        assert "Could not serialize" in result.content[0].text  # type: ignore[union-attr]
        assert "with_schema" in result.content[0].text  # type: ignore[union-attr]

    async def test_no_output_schema_keeps_content_fallback(self):
        """Without an output schema there is no structured-content contract,
        so the content-only fallback stays."""
        async with Client(self._make_server()) as client:
            result = await client.call_tool("without_schema", {})

        assert not result.is_error
        assert result.structured_content is None
        assert "db1" in result.content[0].text  # type: ignore[union-attr]
