"""FastAPI tuple schemas retain resolvable model references."""

import httpx2
import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator, ValidationError
from pydantic import BaseModel

from fastmcp import Client, FastMCP


class Item(BaseModel):
    name: str


async def test_fastapi_tuple_input_schema() -> None:
    app = FastAPI()

    @app.post("/items", operation_id="save_item")
    def save_item(value: tuple[Item, int]) -> dict[str, str]:
        return {"name": value[0].name}

    arguments = {"value": [{"name": "example"}, 2]}
    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.ASGITransport(app)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=app.openapi(), client=http_client)
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            validator = Draft202012Validator(tool.input_schema)
            validator.validate(arguments)
            with pytest.raises(ValidationError):
                validator.validate({"value": [{"name": 42}, 2]})
            with pytest.raises(ValidationError):
                validator.validate({"value": [{"name": "example"}, "invalid"]})

            result = await client.call_tool("save_item", arguments)

    assert result.structured_content == {"name": "example"}


async def test_fastapi_tuple_output_schema() -> None:
    app = FastAPI()

    @app.post("/items", operation_id="get_item")
    def get_item() -> tuple[Item, int]:
        return Item(name="example"), 2

    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.ASGITransport(app)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=app.openapi(), client=http_client)
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            result = await client.call_tool("get_item")

    assert result.structured_content == {"result": [{"name": "example"}, 2]}
    assert tool.output_schema is not None
    Draft202012Validator(tool.output_schema).validate(result.structured_content)


async def test_fastapi_nested_tuple_schemas() -> None:
    class Batch(BaseModel):
        entries: list[tuple[Item, int]]

    app = FastAPI()

    @app.post("/batches", operation_id="save_batch")
    def save_batch(batch: Batch) -> Batch:
        return batch

    arguments = {"entries": [[{"name": "example"}, 2]]}
    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.ASGITransport(app)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=app.openapi(), client=http_client)
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            Draft202012Validator(tool.input_schema).validate(arguments)
            result = await client.call_tool("save_batch", arguments)

    assert result.structured_content == arguments
    assert tool.output_schema is not None
    Draft202012Validator(tool.output_schema).validate(result.structured_content)
