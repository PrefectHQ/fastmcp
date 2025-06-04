import asyncio
from typing import Any

import yaml

from fastmcp import FastMCP


# Define a simple custom serializer
def custom_dict_serializer(data: Any) -> str:
    return yaml.dump(data, width=100, sort_keys=False)


server = FastMCP(name="CustomSerializerExample", tool_serializer=custom_dict_serializer)


@server.tool
def get_example_data() -> dict:
    """Returns some example data."""
    return {"name": "Test", "value": 123, "status": True}


async def example_usage():
    result = await server._mcp_call_tool("get_example_data", {})
    print("Tool Result:")
    print(result)
    print("This is an example of using a custom serializer with FastMCP.")


if __name__ == "__main__":
    asyncio.run(example_usage())
    server.run()
