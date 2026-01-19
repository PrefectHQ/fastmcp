from dataclasses import dataclass

import pytest
from inline_snapshot import snapshot
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel

from fastmcp.tools.tool import Tool, _convert_to_content
from fastmcp.utilities.types import Audio, File, Image


class SampleModel(BaseModel):
    x: int
    y: str


class TestConvertResultToContent:
    """Tests for the _convert_to_content helper function."""

    @pytest.mark.parametrize(
        argnames=("result", "expected"),
        argvalues=[
            (True, "true"),
            ("hello", "hello"),
            (123, "123"),
            (123.45, "123.45"),
            ({"key": "value"}, '{"key":"value"}'),
            (
                SampleModel(x=1, y="hello"),
                '{"x":1,"y":"hello"}',
            ),
        ],
        ids=[
            "boolean",
            "string",
            "integer",
            "float",
            "object",
            "basemodel",
        ],
    )
    def test_convert_singular(self, result, expected):
        """Test that a single item is converted to a TextContent."""
        converted = _convert_to_content(result)
        assert converted == [TextContent(type="text", text=expected)]

    @pytest.mark.parametrize(
        argnames=("result", "expected_text"),
        argvalues=[
            ([None], "[null]"),
            ([None, None], "[null,null]"),
            ([True], "[true]"),
            ([True, False], "[true,false]"),
            (["hello"], '["hello"]'),
            (["hello", "world"], '["hello","world"]'),
            ([123], "[123]"),
            ([123, 456], "[123,456]"),
            ([123.45], "[123.45]"),
            ([123.45, 456.78], "[123.45,456.78]"),
            ([{"key": "value"}], '[{"key":"value"}]'),
            (
                [{"key": "value"}, {"key2": "value2"}],
                '[{"key":"value"},{"key2":"value2"}]',
            ),
            ([SampleModel(x=1, y="hello")], '[{"x":1,"y":"hello"}]'),
            (
                [SampleModel(x=1, y="hello"), SampleModel(x=2, y="world")],
                '[{"x":1,"y":"hello"},{"x":2,"y":"world"}]',
            ),
            ([1, "two", None, {"c": 3}, False], '[1,"two",null,{"c":3},false]'),
        ],
        ids=[
            "none",
            "none_many",
            "boolean",
            "boolean_many",
            "string",
            "string_many",
            "integer",
            "integer_many",
            "float",
            "float_many",
            "object",
            "object_many",
            "basemodel",
            "basemodel_many",
            "mixed",
        ],
    )
    def test_convert_list(self, result, expected_text):
        """Test that a list is converted to a TextContent."""
        converted = _convert_to_content(result)
        assert converted == [TextContent(type="text", text=expected_text)]

    @pytest.mark.parametrize(
        argnames="content_block",
        argvalues=[
            (TextContent(type="text", text="hello")),
            (ImageContent(type="image", data="fakeimagedata", mimeType="image/png")),
            (AudioContent(type="audio", data="fakeaudiodata", mimeType="audio/mpeg")),
            (
                ResourceLink(
                    type="resource_link",
                    name="test resource",
                    uri=AnyUrl("resource://test"),
                )
            ),
            (
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("resource://test"),
                        mimeType="text/plain",
                        text="resource content",
                    ),
                )
            ),
        ],
        ids=["text", "image", "audio", "resource link", "embedded resource"],
    )
    def test_convert_content_block(self, content_block):
        converted = _convert_to_content(content_block)
        assert converted == [content_block]

        converted = _convert_to_content([content_block, content_block])
        assert converted == [content_block, content_block]

    @pytest.mark.parametrize(
        argnames=("result", "expected"),
        argvalues=[
            (
                Image(data=b"fakeimagedata"),
                [
                    ImageContent(
                        type="image", data="ZmFrZWltYWdlZGF0YQ==", mimeType="image/png"
                    )
                ],
            ),
            (
                Audio(data=b"fakeaudiodata"),
                [
                    AudioContent(
                        type="audio", data="ZmFrZWF1ZGlvZGF0YQ==", mimeType="audio/wav"
                    )
                ],
            ),
            (
                File(data=b"filedata", format="octet-stream"),
                [
                    EmbeddedResource(
                        type="resource",
                        resource=BlobResourceContents(
                            uri=AnyUrl("file:///resource.octet-stream"),
                            blob="ZmlsZWRhdGE=",
                            mimeType="application/octet-stream",
                        ),
                    )
                ],
            ),
        ],
        ids=["image", "audio", "file"],
    )
    def test_convert_helpers(self, result, expected):
        converted = _convert_to_content(result)
        assert converted == expected

        converted = _convert_to_content([result, result])
        assert converted == expected * 2

    def test_convert_mixed_content(self):
        result = [
            "hello",
            123,
            123.45,
            {"key": "value"},
            SampleModel(x=1, y="hello"),
            Image(data=b"fakeimagedata"),
            Audio(data=b"fakeaudiodata"),
            ResourceLink(
                type="resource_link",
                name="test resource",
                uri=AnyUrl("resource://test"),
            ),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri=AnyUrl("resource://test"),
                    mimeType="text/plain",
                    text="resource content",
                ),
            ),
        ]

        converted = _convert_to_content(result)

        assert converted == snapshot(
            [
                TextContent(type="text", text="hello"),
                TextContent(type="text", text="123"),
                TextContent(type="text", text="123.45"),
                TextContent(type="text", text='{"key":"value"}'),
                TextContent(type="text", text='{"x":1,"y":"hello"}'),
                ImageContent(
                    type="image", data="ZmFrZWltYWdlZGF0YQ==", mimeType="image/png"
                ),
                AudioContent(
                    type="audio", data="ZmFrZWF1ZGlvZGF0YQ==", mimeType="audio/wav"
                ),
                ResourceLink(
                    name="test resource",
                    uri=AnyUrl("resource://test"),
                    type="resource_link",
                ),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("resource://test"),
                        mimeType="text/plain",
                        text="resource content",
                    ),
                ),
            ]
        )

    def test_empty_list(self):
        """Test that an empty list results in an empty list."""
        result = _convert_to_content([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_empty_dict(self):
        """Test that an empty dictionary is converted to TextContent."""
        result = _convert_to_content({})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "{}"


class TestAutomaticStructuredContent:
    """Tests for automatic structured content generation based on return types."""

    async def test_dict_return_creates_structured_content_without_schema(self):
        """Test that dict returns automatically create structured content even without output schema."""

        def get_user_data(user_id: str) -> dict:
            return {"name": "Alice", "age": 30, "active": True}

        # No explicit output schema provided
        tool = Tool.from_function(get_user_data)

        result = await tool.run({"user_id": "123"})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.structured_content == {"name": "Alice", "age": 30, "active": True}

    async def test_dataclass_return_creates_structured_content_without_schema(self):
        """Test that dataclass returns automatically create structured content even without output schema."""

        @dataclass
        class UserProfile:
            name: str
            age: int
            email: str

        def get_profile(user_id: str) -> UserProfile:
            return UserProfile(name="Bob", age=25, email="bob@example.com")

        # No explicit output schema, but dataclass should still create structured content
        tool = Tool.from_function(get_profile, output_schema=None)

        result = await tool.run({"user_id": "456"})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        # Dataclass should serialize to dict
        assert result.structured_content == {
            "name": "Bob",
            "age": 25,
            "email": "bob@example.com",
        }

    async def test_pydantic_model_return_creates_structured_content_without_schema(
        self,
    ):
        """Test that Pydantic model returns automatically create structured content even without output schema."""

        class UserData(BaseModel):
            username: str
            score: int
            verified: bool

        def get_user_stats(user_id: str) -> UserData:
            return UserData(username="charlie", score=100, verified=True)

        # Explicitly set output schema to None to test automatic structured content
        tool = Tool.from_function(get_user_stats, output_schema=None)

        result = await tool.run({"user_id": "789"})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        # Pydantic model should serialize to dict
        assert result.structured_content == {
            "username": "charlie",
            "score": 100,
            "verified": True,
        }

    async def test_self_referencing_dataclass_not_wrapped(self):
        """Test that self-referencing dataclasses are not wrapped in result field."""

        @dataclass
        class ReturnThing:
            value: int
            stuff: list["ReturnThing"]

        def return_things() -> ReturnThing:
            return ReturnThing(value=123, stuff=[ReturnThing(value=456, stuff=[])])

        tool = Tool.from_function(return_things)

        result = await tool.run({})

        # Should have structured content without wrapping
        assert result.structured_content is not None
        # Should NOT be wrapped in "result" field
        assert "result" not in result.structured_content
        # Should have the actual data directly
        assert result.structured_content == {
            "value": 123,
            "stuff": [{"value": 456, "stuff": []}],
        }

    async def test_self_referencing_pydantic_model_has_type_object_at_root(self):
        """Test that self-referencing Pydantic models have type: object at root.

        MCP spec requires outputSchema to have "type": "object" at the root level.
        Pydantic generates schemas with $ref at root for self-referential models,
        which violates this requirement. FastMCP should resolve the $ref.

        Regression test for issue #2455.
        """

        class Issue(BaseModel):
            id: str
            title: str
            dependencies: list["Issue"] = []
            dependents: list["Issue"] = []

        def get_issue(issue_id: str) -> Issue:
            return Issue(id=issue_id, title="Test")

        tool = Tool.from_function(get_issue)

        # The output schema should have "type": "object" at root, not $ref
        assert tool.output_schema is not None
        assert tool.output_schema.get("type") == "object"
        assert "properties" in tool.output_schema
        # Should still have $defs for nested references
        assert "$defs" in tool.output_schema
        # Should NOT have $ref at root level
        assert "$ref" not in tool.output_schema

    async def test_self_referencing_model_outputschema_mcp_compliant(self):
        """Test that self-referencing model schemas are MCP spec compliant.

        The MCP spec requires:
        - type: "object" at root level
        - properties field
        - required field (optional)

        This ensures clients can properly validate the schema.

        Regression test for issue #2455.
        """

        class Node(BaseModel):
            id: str
            children: list["Node"] = []

        def get_node() -> Node:
            return Node(id="1")

        tool = Tool.from_function(get_node)

        # Schema should be MCP-compliant
        assert tool.output_schema is not None
        assert tool.output_schema.get("type") == "object", (
            "MCP spec requires 'type': 'object' at root"
        )
        assert "properties" in tool.output_schema
        assert "id" in tool.output_schema["properties"]
        assert "children" in tool.output_schema["properties"]
        # Required should include 'id'
        assert "id" in tool.output_schema.get("required", [])

    async def test_int_return_no_structured_content_without_schema(self):
        """Test that int returns don't create structured content without output schema."""

        def calculate_sum(a: int, b: int):
            """No return annotation."""
            return a + b

        # No output schema
        tool = Tool.from_function(calculate_sum)

        result = await tool.run({"a": 5, "b": 3})

        # Should only have content, no structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "8"
        assert result.structured_content is None

    async def test_str_return_no_structured_content_without_schema(self):
        """Test that str returns don't create structured content without output schema."""

        def get_greeting(name: str):
            """No return annotation."""
            return f"Hello, {name}!"

        # No output schema
        tool = Tool.from_function(get_greeting)

        result = await tool.run({"name": "World"})

        # Should only have content, no structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Hello, World!"
        assert result.structured_content is None

    async def test_list_return_no_structured_content_without_schema(self):
        """Test that list returns don't create structured content without output schema."""

        def get_numbers():
            """No return annotation."""
            return [1, 2, 3, 4, 5]

        # No output schema
        tool = Tool.from_function(get_numbers)

        result = await tool.run({})

        assert result.structured_content is None
        assert result.content == snapshot(
            [TextContent(type="text", text="[1,2,3,4,5]")]
        )

    async def test_audio_return_creates_no_structured_content(self):
        """Test that audio returns don't create structured content."""

        def get_audio() -> AudioContent:
            """No return annotation."""
            return Audio(data=b"fakeaudiodata").to_audio_content()

        # No output schema
        tool = Tool.from_function(get_audio)

        result = await tool.run({})

        assert result.content == snapshot(
            [
                AudioContent(
                    type="audio", data="ZmFrZWF1ZGlvZGF0YQ==", mimeType="audio/wav"
                )
            ]
        )
        assert result.structured_content is None

    async def test_int_return_with_schema_creates_structured_content(self):
        """Test that int returns DO create structured content when there's an output schema."""

        def calculate_sum(a: int, b: int) -> int:
            """With return annotation."""
            return a + b

        # Output schema should be auto-generated from annotation
        tool = Tool.from_function(calculate_sum)
        assert tool.output_schema is not None

        result = await tool.run({"a": 5, "b": 3})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "8"
        assert result.structured_content == {"result": 8}

    async def test_client_automatic_deserialization_with_dict_result(self):
        """Test that clients automatically deserialize dict results from structured content."""
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @mcp.tool
        def get_user_info(user_id: str) -> dict:
            return {"name": "Alice", "age": 30, "active": True}

        async with Client(mcp) as client:
            result = await client.call_tool("get_user_info", {"user_id": "123"})

            # Client should provide the deserialized data
            assert result.data == {"name": "Alice", "age": 30, "active": True}
            assert result.structured_content == {
                "name": "Alice",
                "age": 30,
                "active": True,
            }
            assert len(result.content) == 1

    async def test_client_automatic_deserialization_with_dataclass_result(self):
        """Test that clients automatically deserialize dataclass results from structured content."""
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @dataclass
        class UserProfile:
            name: str
            age: int
            verified: bool

        @mcp.tool
        def get_profile(user_id: str) -> UserProfile:
            return UserProfile(name="Bob", age=25, verified=True)

        async with Client(mcp) as client:
            result = await client.call_tool("get_profile", {"user_id": "456"})

            # Client should deserialize back to a dataclass (but type name is lost with title pruning)
            assert result.data.__class__.__name__ == "Root"
            assert result.data.name == "Bob"
            assert result.data.age == 25
            assert result.data.verified is True
