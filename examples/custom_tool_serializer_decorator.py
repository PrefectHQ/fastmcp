"""Example of custom tool serialization using ToolResult and a wrapper decorator.

This pattern provides explicit control over how tool outputs are serialized,
making the serialization visible in each tool's code.
"""

import asyncio
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

import yaml

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult


def with_serializer(serializer: Callable[[Any], str]):
    """Decorator to apply custom serialization to tool output."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            return ToolResult(content=serializer(result), structured_content=result)

        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            result = await fn(*args, **kwargs)
            return ToolResult(content=serializer(result), structured_content=result)

        return async_wrapper if inspect.iscoroutinefunction(fn) else wrapper

    return decorator


# Create reusable serializer decorators
with_yaml = with_serializer(lambda d: yaml.dump(d, width=100, sort_keys=False))

server = FastMCP(name="CustomSerializerExample")


@server.tool
@with_yaml
def get_example_data() -> dict:
    """Returns some example data serialized as YAML."""
    return {"name": "Test", "value": 123, "status": True}


@server.tool
def get_json_data() -> dict:
    """Returns data with default JSON serialization."""
    return {"format": "json", "data": [1, 2, 3]}


async def example_usage():
    # YAML serialized tool
    yaml_result = await server._call_tool_mcp("get_example_data", {})
    print("YAML Tool Result:")
    print(yaml_result)
    print()

    # Default JSON serialized tool
    json_result = await server._call_tool_mcp("get_json_data", {})
    print("JSON Tool Result:")
    print(json_result)


if __name__ == "__main__":
    asyncio.run(example_usage())
    server.run()
