"""SamplingTool for use during LLM sampling requests."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent
from mcp.types import Tool as SDKTool
from pydantic import ConfigDict

from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import ToolResult
from fastmcp.tools.tool_transform import TransformedTool
from fastmcp.utilities.types import FastMCPBaseModel


class SamplingTool(FastMCPBaseModel):
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
    sequential: bool = False

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
        sequential: bool = False,
    ) -> SamplingTool:
        """Create a SamplingTool from a function.

        The function's signature is analyzed to generate a JSON schema for
        the tool's parameters. Type hints are used to determine parameter types.

        Args:
            fn: The function to create a tool from.
            name: Optional name override. Defaults to the function's name.
            description: Optional description override. Defaults to the function's docstring.
            sequential: If True, this tool requires sequential execution and prevents
                parallel execution of all tools in the batch. Set to True for tools
                with shared state, file writes, or other operations that cannot run
                concurrently. Defaults to False.

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
            sequential=sequential,
        )

    @classmethod
    def from_callable_tool(
        cls,
        tool: FunctionTool | TransformedTool,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> SamplingTool:
        """Create a SamplingTool from a FunctionTool or TransformedTool.

        Reuses existing server tools in sampling contexts. For TransformedTool,
        the tool's .run() method is used to ensure proper argument transformation,
        and the ToolResult is automatically unwrapped.

        Args:
            tool: A FunctionTool or TransformedTool to convert.
            name: Optional name override. Defaults to tool.name.
            description: Optional description override. Defaults to tool.description.

        Raises:
            TypeError: If the tool is not a FunctionTool or TransformedTool.
        """
        # Validate that the tool is a supported type
        if not isinstance(tool, (FunctionTool, TransformedTool)):
            raise TypeError(
                f"Expected FunctionTool or TransformedTool, got {type(tool).__name__}. "
                "Only callable tools can be converted to SamplingTools."
            )

        # Both FunctionTool and TransformedTool need .run() to ensure proper
        # result processing (serializers, output_schema, wrap-result flags)
        async def wrapper(**kwargs: Any) -> Any:
            result = await tool.run(kwargs)
            # Unwrap ToolResult - extract the actual value
            if isinstance(result, ToolResult):
                # If there's structured_content, use that
                if result.structured_content is not None:
                    # Check tool's schema - this is the source of truth
                    if tool.output_schema and tool.output_schema.get(
                        "x-fastmcp-wrap-result"
                    ):
                        # Tool wraps results: {"result": value} -> value
                        return result.structured_content.get("result")
                    else:
                        # No wrapping: use structured_content directly
                        return result.structured_content
                # Otherwise, extract from text content
                if result.content and len(result.content) > 0:
                    first_content = result.content[0]
                    if isinstance(first_content, TextContent):
                        return first_content.text
            return result

        fn = wrapper

        # Extract the callable function, name, description, and parameters
        return cls(
            name=name or tool.name,
            description=description or tool.description,
            parameters=tool.parameters,
            fn=fn,
        )
