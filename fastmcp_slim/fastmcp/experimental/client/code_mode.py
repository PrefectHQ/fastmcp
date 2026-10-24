"""Client-side CodeMode: discovery and sandboxed execution over remote tools.

`CodeModeClient` wraps a `Client` or `ClientGroup` and presents the same
discovery + execute meta-tools as the server-side `CodeMode` transform. The
sandbox runs locally; each `call_tool` inside it is an ordinary `tools/call`
on the wrapped client, so servers never see the generated code and need no
CodeMode support of their own.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Annotated, Any

import mcp_types
from pydantic import Field

from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.group import ClientGroup
from fastmcp.client.mixins.tools import _parse_call_tool_result
from fastmcp.exceptions import FastMCPError, NotFoundError, ToolError
from fastmcp.experimental.transforms.code_mode import (
    _DEFAULT_EXECUTE_DESCRIPTION,
    _EXECUTE_CODE_DESCRIPTION,
    DiscoveryToolFactory,
    MontySandboxProvider,
    SandboxProvider,
    _build_discovery_tools,
    _default_discovery_tools,
    _error_detail,
    _legible_call_error,
    _unwrap_tool_result,
)
from fastmcp.server.context import Context
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.components import get_fastmcp_metadata


def _tool_from_mcp(tool: mcp_types.Tool) -> Tool:
    """Describe a remote tool as a local `Tool` the discovery tools can render."""
    return Tool(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        parameters=tool.input_schema,
        output_schema=tool.output_schema,
        annotations=tool.annotations,
        icons=tool.icons,
        meta=tool.meta,
        tags=set(get_fastmcp_metadata(tool.meta).get("tags", [])),
    )


def _error_result(message: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        is_error=True,
    )


class CodeModeClient:
    """Present a client's tools through CodeMode discovery and execute tools.

    `list_tools()` returns only the meta-tools (by default `search`,
    `get_schema`, and `execute`). `call_tool()` runs them locally: discovery
    reads the wrapped client's live tool list, and `execute` runs Python in a
    sandbox where `call_tool(name, params)` calls the wrapped client.

    Wrapping a `ClientGroup` lets one `execute` block chain tools from several
    servers, using the group's namespaced tool names.

    Args:
        client: The connected (or connectable) `Client` or `ClientGroup` whose
            tools the meta-tools expose.
        sandbox_provider: Executes generated code. Defaults to
            `MontySandboxProvider`, which requires `fastmcp[code-mode]`.
        discovery_tools: Discovery tool factories, as for `CodeMode`.
            Defaults to `Search` and `GetSchemas`.
        execute_tool_name: Name of the execute tool.
        execute_description: Description of the execute tool.
        max_tool_calls: Maximum `call_tool` invocations per `execute`;
            `None` removes the limit.
    """

    def __init__(
        self,
        client: Client[Any] | ClientGroup,
        *,
        sandbox_provider: SandboxProvider | None = None,
        discovery_tools: list[DiscoveryToolFactory] | None = None,
        execute_tool_name: str = "execute",
        execute_description: str | None = None,
        max_tool_calls: int | None = 50,
    ) -> None:
        self._client = client
        self.sandbox_provider = sandbox_provider or MontySandboxProvider()
        self.max_tool_calls = max_tool_calls

        factories = (
            discovery_tools
            if discovery_tools is not None
            else _default_discovery_tools()
        )
        tools = _build_discovery_tools(factories, self._get_catalog, execute_tool_name)
        tools.append(
            self._make_execute_tool(
                execute_tool_name, execute_description or _DEFAULT_EXECUTE_DESCRIPTION
            )
        )
        self._tools = {tool.name: tool for tool in tools}

    @property
    def client(self) -> Client[Any] | ClientGroup:
        """The wrapped client or group."""
        return self._client

    async def __aenter__(self) -> CodeModeClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return await self._client.__aexit__(exc_type, exc_value, traceback)

    async def list_tools(self) -> list[mcp_types.Tool]:
        """List the meta-tools in place of the wrapped client's tools."""
        return [tool.to_mcp_tool() for tool in self._tools.values()]

    async def call_tool_mcp(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.CallToolResult:
        """Run a meta-tool and return its MCP result.

        As with `Client.call_tool_mcp`, a failing tool produces a result with
        `is_error` set rather than raising.
        """
        tool = self._tools.get(name)
        if tool is None:
            return _error_result(f"Unknown tool: {name!r}")
        try:
            result = await tool.run(arguments or {})
        except FastMCPError as exc:
            return _error_result(str(exc))
        except Exception as exc:
            return _error_result(f"Error calling tool {name!r}: {exc}")
        return mcp_types.CallToolResult(
            content=result.content,
            structured_content=result.structured_content,
            is_error=result.is_error,
            _meta=result.meta,  # type: ignore[call-arg]  # _meta is the alias for meta
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        raise_on_error: bool = True,
    ) -> CallToolResult:
        """Run a meta-tool and parse its result as `Client.call_tool` does.

        Raises:
            ToolError: If the tool fails and `raise_on_error` is true.
        """
        result = await self.call_tool_mcp(name, arguments)
        return await _parse_call_tool_result(
            name=name,
            result=result,
            tool_output_schemas={
                tool.name: tool.output_schema for tool in self._tools.values()
            },
            list_tools_fn=self.list_tools,
            raise_on_error=raise_on_error,
        )

    async def _get_catalog(self, ctx: Context | None = None) -> Sequence[Tool]:
        return [_tool_from_mcp(tool) for tool in await self._client.list_tools()]

    async def _call_error(self, tool_name: str, result: ToolResult) -> str:
        catalog = {tool.name: tool for tool in await self._get_catalog()}
        tool = catalog.get(tool_name) or Tool(name=tool_name, parameters={})
        return _legible_call_error(tool_name, tool, _error_detail(result))

    def _make_execute_tool(self, name: str, description: str) -> Tool:
        code_mode = self

        async def execute(
            code: Annotated[str, Field(description=_EXECUTE_CODE_DESCRIPTION)],
        ) -> Any:
            """Execute tool calls using Python code."""

            call_count = 0

            async def call_tool(tool_name: str, params: dict[str, Any]) -> Any:
                nonlocal call_count
                max_tool_calls = code_mode.max_tool_calls
                if max_tool_calls is not None:
                    call_count += 1
                    if call_count > max_tool_calls:
                        raise ToolError(
                            f"Tool call limit exceeded: at most {max_tool_calls} "
                            "call_tool() invocations are allowed per execute()."
                        )

                try:
                    raw = await code_mode._client.call_tool_mcp(tool_name, params)
                except KeyError:
                    # A ClientGroup has no route for this name.
                    raise NotFoundError(f"Unknown tool: {tool_name}") from None
                result = ToolResult.from_mcp_result(raw)
                if result.is_error:
                    raise ToolError(await code_mode._call_error(tool_name, result))
                return _unwrap_tool_result(result)

            return await code_mode.sandbox_provider.run(
                code, external_functions={"call_tool": call_tool}
            )

        return Tool.from_function(fn=execute, name=name, description=description)


__all__ = ["CodeModeClient"]
