from typing import Any

from mcp.types import CallToolResult
from pydantic import BaseModel, Field

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.contrib.mcp_mixin.mcp_mixin import (
    _DEFAULT_SEPARATOR_TOOL,
    MCPMixin,
    mcp_tool,
)


class CallToolRequest(BaseModel):
    """A class to represent a request to call a tool with specific arguments."""

    tool: str = Field(description="The name of the tool to call.")
    arguments: dict[str, Any] = Field(
        description="A dictionary containing the arguments for the tool call."
    )


class CallToolRequestResult(CallToolResult):
    """
    A class to represent the result of a bulk tool call.
    It extends CallToolResult to include information about the requested tool call.
    """

    tool: str = Field(description="The name of the tool that was called.")
    arguments: dict[str, Any] = Field(
        description="The arguments used for the tool call."
    )

    @classmethod
    def from_call_tool_result(
        cls, result: CallToolResult, tool: str, arguments: dict[str, Any]
    ) -> "CallToolRequestResult":
        """
        Create a CallToolRequestResult from a CallToolResult.
        """
        return cls(
            tool=tool,
            arguments=arguments,
            isError=result.isError,
            content=result.content,
        )


class BulkToolCaller(MCPMixin):
    """
    A class to provide a "bulk tool call" tool for a FastMCP server
    """

    def register_tools(
        self,
        mcp_server: "FastMCP",
        prefix: str | None = None,
        separator: str = _DEFAULT_SEPARATOR_TOOL,
    ) -> None:
        """
        Register the tools provided by this class with the given MCP server.
        """
        self.connection = FastMCPTransport(mcp_server)

        super().register_tools(mcp_server=mcp_server)

    @mcp_tool()
    async def call_tools_bulk(
        self, tool_calls: list[CallToolRequest], continue_on_error: bool = True
    ) -> list[CallToolRequestResult]:
        """
        Call multiple tools registered on this MCP server in a single request. Each call can
         be for a different tool and can include different arguments. Useful for speeding up
         what would otherwise take several individual tool calls.
        """
        results = []

        for tool_call in tool_calls:
            result = await self._call_tool(tool_call.tool, tool_call.arguments)

            results.append(result)

            if result.isError and not continue_on_error:
                return results

        return results

    @mcp_tool()
    async def call_tool_bulk(
        self,
        tool: str,
        tool_arguments: list[dict[str, str | int | float | bool | None]],
        continue_on_error: bool = True,
    ) -> list[CallToolRequestResult]:
        """
        Call a single tool registered on this MCP server multiple times with a single request.
         Each call can include different arguments. Useful for speeding up what would otherwise
         take several individual tool calls.

        Args:
            tool: The name of the tool to call.
            tool_arguments: A list of dictionaries, where each dictionary contains the arguments for an individual run of the tool.
        """
        results = []

        for tool_call_arguments in tool_arguments:
            result = await self._call_tool(tool, tool_call_arguments)

            results.append(result)

            if result.isError and not continue_on_error:
                return results

        return results

    async def _call_tool(
        self, tool: str, arguments: dict[str, Any]
    ) -> CallToolRequestResult:
        """
        Helper method to call a tool with the provided arguments.
        """

        async with Client(self.connection) as client:
            result = await client.call_tool_mcp(name=tool, arguments=arguments)

            return CallToolRequestResult(
                tool=tool,
                arguments=arguments,
                isError=result.isError,
                content=result.content,
            )
