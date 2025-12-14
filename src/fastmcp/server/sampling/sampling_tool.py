"""SamplingTool for use during LLM sampling requests."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from mcp.types import Tool as SDKTool
from pydantic import BaseModel, ConfigDict

from fastmcp.tools.tool import ParsedFunction


class SamplingTool(BaseModel):
    """A tool that can be used during LLM sampling.

    SamplingTools bundle a tool's schema (name, description, parameters) with
    an executor function, enabling servers to execute agentic workflows where
    the LLM can request tool calls during sampling.

    In most cases, pass functions directly to ctx.sample():

        def search(query: str) -> str:
            '''Search the web.'''
            return web_search(query)

        result = await context.sample(
            messages="Find info about Python",
            tools=[search],  # Plain functions work directly
        )

    Create a SamplingTool explicitly when you need custom name/description:

        tool = SamplingTool.from_function(search, name="web_search")
    """

    name: str
    description: str | None = None
    parameters: dict[str, Any]
    fn: Callable[..., Any]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    async def run(self, arguments: dict[str, Any] | None = None) -> Any:
        """Execute the tool with the given arguments.

        Args:
            arguments: Dictionary of arguments to pass to the tool function.

        Returns:
            The result of executing the tool function.
        """
        if arguments is None:
            arguments = {}

        result = self.fn(**arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _to_sdk_tool(self) -> SDKTool:
        """Convert to an mcp.types.Tool for SDK compatibility.

        This is used internally when passing tools to the MCP SDK's
        create_message() method.
        """
        return SDKTool(
            name=self.name,
            description=self.description,
            inputSchema=self.parameters,
        )

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> SamplingTool:
        """Create a SamplingTool from a function.

        The function's signature is analyzed to generate a JSON schema for
        the tool's parameters. Type hints are used to determine parameter types.

        Args:
            fn: The function to create a tool from.
            name: Optional name override. Defaults to the function's name.
            description: Optional description override. Defaults to the function's docstring.

        Returns:
            A SamplingTool wrapping the function.

        Raises:
            ValueError: If the function is a lambda without a name override.
        """
        parsed = ParsedFunction.from_function(fn, validate=True)

        if name is None and parsed.name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        return cls(
            name=name or parsed.name,
            description=description or parsed.description,
            parameters=parsed.input_schema,
            fn=parsed.fn,
        )
