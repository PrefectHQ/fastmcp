"""Tests for tool parameters and validation."""

import base64
import datetime
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import pytest
from mcp.types import (
    ImageContent,
)
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.utilities.types import Image


def _normalize_anyof_order(schema):
    """Normalize the order of items in anyOf arrays for consistent comparison."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            schema = schema.copy()
            schema["anyOf"] = sorted(schema["anyOf"], key=str)
        return {k: _normalize_anyof_order(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_normalize_anyof_order(item) for item in schema]
    return schema


class PersonTypedDict(TypedDict):
    name: str
    age: int


class PersonModel(BaseModel):
    name: str
    age: int


@dataclass
class PersonDataclass:
    name: str
    age: int


class TestToolParameters:
    async def test_parameter_descriptions_with_field_annotations(self):
        mcp = FastMCP("Test Server")

        @mcp.tool
        def greet(
            name: Annotated[str, Field(description="The name to greet")],
            title: Annotated[str, Field(description="Optional title", default="")],
        ) -> str:
            """A greeting tool"""
            return f"Hello {title} {name}"

        tools = await mcp.list_tools()
        assert len(tools) == 1
        tool = tools[0]

        properties = tool.parameters["properties"]
        assert "name" in properties
        assert properties["name"]["description"] == "The name to greet"
        assert "title" in properties
        assert properties["title"]["description"] == "Optional title"
        assert properties["title"]["default"] == ""
        assert tool.parameters["required"] == ["name"]

    async def test_parameter_descriptions_with_field_defaults(self):
        mcp = FastMCP("Test Server")

        @mcp.tool
        def greet(
            name: str = Field(description="The name to greet"),
            title: str = Field(description="Optional title", default=""),
        ) -> str:
            """A greeting tool"""
            return f"Hello {title} {name}"

        tools = await mcp.list_tools()
        assert len(tools) == 1
        tool = tools[0]

        properties = tool.parameters["properties"]
        assert "name" in properties
        assert properties["name"]["description"] == "The name to greet"
        assert "title" in properties
        assert properties["title"]["description"] == "Optional title"
        assert properties["title"]["default"] == ""
        assert tool.parameters["required"] == ["name"]

    async def test_tool_with_bytes_input(self):
        mcp = FastMCP()

        @mcp.tool
        def process_image(image: bytes) -> Image:
            return Image(data=image)

        result = await mcp.call_tool("process_image", {"image": b"fake png data"})
        assert result.structured_content is None
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], ImageContent)
        assert result.content[0].mimeType == "image/png"
        assert result.content[0].data == base64.b64encode(b"fake png data").decode()

    async def test_tool_with_invalid_input(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def my_tool(x: int) -> int:
            return x + 1

        with pytest.raises(
            ValidationError,
            match="Input should be a valid integer",
        ):
            await mcp.call_tool("my_tool", {"x": "not an int"})

    async def test_tool_int_coercion(self):
        """Test that string ints are coerced by default."""
        mcp = FastMCP()

        @mcp.tool
        def add_one(x: int) -> int:
            return x + 1

        result = await mcp.call_tool("add_one", {"x": "42"})
        assert result.structured_content == {"result": 43}

    async def test_tool_bool_coercion(self):
        """Test that string bools are coerced by default."""
        mcp = FastMCP()

        @mcp.tool
        def toggle(flag: bool) -> bool:
            return not flag

        result = await mcp.call_tool("toggle", {"flag": "true"})
        assert result.structured_content == {"result": False}

        result = await mcp.call_tool("toggle", {"flag": "false"})
        assert result.structured_content == {"result": True}

    async def test_annotated_field_validation(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Annotated[int, Field(ge=1)]) -> None:
            pass

        with pytest.raises(
            ValidationError,
            match="Input should be greater than or equal to 1",
        ):
            await mcp.call_tool("analyze", {"x": 0})

    async def test_default_field_validation(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field(ge=1)) -> None:
            pass

        with pytest.raises(
            ValidationError,
            match="Input should be greater than or equal to 1",
        ):
            await mcp.call_tool("analyze", {"x": 0})

    async def test_default_field_is_still_required_if_no_default_specified(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field()) -> None:
            pass

        with pytest.raises(ValidationError, match="missing"):
            await mcp.call_tool("analyze", {})

    async def test_literal_type_validation_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> None:
            pass

        with pytest.raises(
            ValidationError,
            match="Input should be 'a' or 'b'",
        ):
            await mcp.call_tool("analyze", {"x": "c"})

    async def test_literal_type_validation_success(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> str:
            return x

        result = await mcp.call_tool("analyze", {"x": "a"})
        assert result.structured_content == {"result": "a"}

    async def test_enum_type_validation_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        class MyEnum(Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        @mcp.tool
        def analyze(x: MyEnum) -> str:
            return x.value

        with pytest.raises(
            ValidationError,
            match="Input should be 'red', 'green' or 'blue'",
        ):
            await mcp.call_tool("analyze", {"x": "some-color"})

    async def test_enum_type_validation_success(self):
        mcp = FastMCP()

        class MyEnum(Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        @mcp.tool
        def analyze(x: MyEnum) -> str:
            return x.value

        result = await mcp.call_tool("analyze", {"x": "red"})
        assert result.structured_content == {"result": "red"}

    async def test_union_type_validation(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int | float) -> str:
            return str(x)

        result = await mcp.call_tool("analyze", {"x": 1})
        assert result.structured_content == {"result": "1"}

        result = await mcp.call_tool("analyze", {"x": 1.0})
        assert result.structured_content == {"result": "1.0"}

        with pytest.raises(
            ValidationError,
            match="Input should be a valid",
        ):
            await mcp.call_tool("analyze", {"x": "not a number"})

    async def test_path_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            assert isinstance(path, Path)
            return str(path)

        test_path = Path("tmp") / "test.txt"

        result = await mcp.call_tool("send_path", {"path": str(test_path)})
        assert result.structured_content == {"result": str(test_path)}

    async def test_path_type_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            return str(path)

        with pytest.raises(ValidationError, match="Input is not a valid path"):
            await mcp.call_tool("send_path", {"path": 1})

    async def test_uuid_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            assert isinstance(x, uuid.UUID)
            return str(x)

        test_uuid = uuid.uuid4()

        result = await mcp.call_tool("send_uuid", {"x": test_uuid})
        assert result.structured_content == {"result": str(test_uuid)}

    async def test_uuid_type_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            return str(x)

        with pytest.raises(ValidationError, match="Input should be a valid UUID"):
            await mcp.call_tool("send_uuid", {"x": "not a uuid"})

    async def test_datetime_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        result = await mcp.call_tool("send_datetime", {"x": dt})
        assert result.structured_content == {"result": dt.isoformat()}

    async def test_datetime_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        result = await mcp.call_tool("send_datetime", {"x": "2021-01-01T00:00:00"})
        assert result.structured_content == {"result": "2021-01-01T00:00:00"}

    async def test_datetime_type_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        with pytest.raises(ValidationError, match="Input should be a valid datetime"):
            await mcp.call_tool("send_datetime", {"x": "not a datetime"})

    async def test_date_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        result = await mcp.call_tool("send_date", {"x": datetime.date.today()})
        assert result.structured_content == {
            "result": datetime.date.today().isoformat()
        }

    async def test_date_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        result = await mcp.call_tool("send_date", {"x": "2021-01-01"})
        assert result.structured_content == {"result": "2021-01-01"}

    async def test_timedelta_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        result = await mcp.call_tool(
            "send_timedelta", {"x": datetime.timedelta(days=1)}
        )
        assert result.structured_content == {"result": "1 day, 0:00:00"}

    async def test_timedelta_type_parse_int(self):
        """Test that int input is coerced to timedelta (seconds)."""
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        result = await mcp.call_tool("send_timedelta", {"x": 1000})
        assert result.structured_content is not None
        result_str = result.structured_content["result"]
        assert (
            "0:16:40" in result_str or "16:40" in result_str
        )  # 1000 seconds = 16 minutes 40 seconds

    async def test_annotated_string_description(self):
        mcp = FastMCP()

        @mcp.tool
        def f(x: Annotated[int, "A number"]):
            return x

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].parameters["properties"]["x"]["description"] == "A number"
