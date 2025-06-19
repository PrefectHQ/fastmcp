from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import (
    METHOD_NOT_FOUND,
    BlobResourceContents,
    GetPromptResult,
    TextResourceContents,
)
from pydantic.networks import AnyUrl

from fastmcp.client import Client
from fastmcp.exceptions import NotFoundError, ResourceError, ToolError
from fastmcp.prompts import Prompt, PromptMessage
from fastmcp.prompts.prompt import PromptArgument
from fastmcp.prompts.prompt_manager import PromptManager
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.resources.resource_manager import ResourceManager
from fastmcp.server.context import Context
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool
from fastmcp.tools.tool_manager import ToolManager
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import MCPContent

if TYPE_CHECKING:
    from fastmcp.server import Context

logger = get_logger(__name__)


class ProxyToolManager(ToolManager):
    """A ToolManager that sources its tools from a remote client in addition to local and mounted tools."""

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self.client = client

    async def get_tools(self) -> dict[str, Tool]:
        """Gets the unfiltered tool inventory including local, mounted, and proxy tools."""
        # First get local and mounted tools from parent
        all_tools = await super().get_tools()

        # Then add proxy tools, but don't overwrite existing ones
        try:
            async with self.client:
                client_tools = await self.client.list_tools()
                for tool in client_tools:
                    if tool.name not in all_tools:
                        all_tools[tool.name] = ProxyTool.from_mcp_tool(
                            self.client, tool
                        )
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                pass  # No tools available from proxy
            else:
                raise e

        return all_tools

    async def list_tools(self) -> list[Tool]:
        """Gets the filtered list of tools including local, mounted, and proxy tools."""
        tools_dict = await self.get_tools()
        return list(tools_dict.values())

    async def call_tool(self, key: str, arguments: dict[str, Any]) -> list[MCPContent]:
        """Calls a tool, trying local/mounted first, then proxy if not found."""
        try:
            # First try local and mounted tools
            return await super().call_tool(key, arguments)
        except NotFoundError:
            # If not found locally, try proxy
            async with self.client:
                return await self.client.call_tool(key, arguments)


class ProxyResourceManager(ResourceManager):
    """A ResourceManager that sources its resources from a remote client in addition to local and mounted resources."""

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self.client = client

    async def get_resources(self) -> dict[str, Resource]:
        """Gets the unfiltered resource inventory including local, mounted, and proxy resources."""
        # First get local and mounted resources from parent
        all_resources = await super().get_resources()

        # Then add proxy resources, but don't overwrite existing ones
        try:
            async with self.client:
                client_resources = await self.client.list_resources()
                for resource in client_resources:
                    if str(resource.uri) not in all_resources:
                        all_resources[str(resource.uri)] = (
                            ProxyResource.from_mcp_resource(self.client, resource)
                        )
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                pass  # No resources available from proxy
            else:
                raise e

        return all_resources

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Gets the unfiltered template inventory including local, mounted, and proxy templates."""
        # First get local and mounted templates from parent
        all_templates = await super().get_resource_templates()

        # Then add proxy templates, but don't overwrite existing ones
        try:
            async with self.client:
                client_templates = await self.client.list_resource_templates()
                for template in client_templates:
                    if template.uriTemplate not in all_templates:
                        all_templates[template.uriTemplate] = (
                            ProxyTemplate.from_mcp_template(self.client, template)
                        )
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                pass  # No templates available from proxy
            else:
                raise e

        return all_templates

    async def list_resources(self) -> list[Resource]:
        """Gets the filtered list of resources including local, mounted, and proxy resources."""
        resources_dict = await self.get_resources()
        return list(resources_dict.values())

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """Gets the filtered list of templates including local, mounted, and proxy templates."""
        templates_dict = await self.get_resource_templates()
        return list(templates_dict.values())

    async def read_resource(self, uri: AnyUrl | str) -> str | bytes:
        """Reads a resource, trying local/mounted first, then proxy if not found."""
        try:
            # First try local and mounted resources
            return await super().read_resource(uri)
        except NotFoundError:
            # If not found locally, try proxy
            async with self.client:
                result = await self.client.read_resource(uri)
                if isinstance(result[0], TextResourceContents):
                    return result[0].text
                elif isinstance(result[0], BlobResourceContents):
                    return result[0].blob
                else:
                    raise ResourceError(f"Unsupported content type: {type(result[0])}")


class ProxyPromptManager(PromptManager):
    """A PromptManager that sources its prompts from a remote client in addition to local and mounted prompts."""

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self.client = client

    async def get_prompts(self) -> dict[str, Prompt]:
        """Gets the unfiltered prompt inventory including local, mounted, and proxy prompts."""
        # First get local and mounted prompts from parent
        all_prompts = await super().get_prompts()

        # Then add proxy prompts, but don't overwrite existing ones
        try:
            async with self.client:
                client_prompts = await self.client.list_prompts()
                for prompt in client_prompts:
                    if prompt.name not in all_prompts:
                        all_prompts[prompt.name] = ProxyPrompt.from_mcp_prompt(
                            self.client, prompt
                        )
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                pass  # No prompts available from proxy
            else:
                raise e

        return all_prompts

    async def list_prompts(self) -> list[Prompt]:
        """Gets the filtered list of prompts including local, mounted, and proxy prompts."""
        prompts_dict = await self.get_prompts()
        return list(prompts_dict.values())

    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> GetPromptResult:
        """Renders a prompt, trying local/mounted first, then proxy if not found."""
        try:
            # First try local and mounted prompts
            return await super().render_prompt(name, arguments)
        except NotFoundError:
            # If not found locally, try proxy
            async with self.client:
                result = await self.client.get_prompt(name, arguments)
                return result


class ProxyTool(Tool):
    """
    A Tool that represents and executes a tool on a remote server.
    """

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self._client = client

    @classmethod
    def from_mcp_tool(cls, client: Client, mcp_tool: mcp.types.Tool) -> ProxyTool:
        """Factory method to create a ProxyTool from a raw MCP tool schema."""
        return cls(
            client=client,
            name=mcp_tool.name,
            description=mcp_tool.description,
            parameters=mcp_tool.inputSchema,
            annotations=mcp_tool.annotations,
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> list[MCPContent]:
        """Executes the tool by making a call through the client."""
        # This is where the remote execution logic lives.
        async with self._client:
            result = await self._client.call_tool_mcp(
                name=self.name,
                arguments=arguments,
            )
        if result.isError:
            raise ToolError(cast(mcp.types.TextContent, result.content[0]).text)
        return result.content


class ProxyResource(Resource):
    """
    A Resource that represents and reads a resource from a remote server.
    """

    _client: Client
    _value: str | bytes | None = None

    def __init__(self, client: Client, *, _value: str | bytes | None = None, **kwargs):
        super().__init__(**kwargs)
        self._client = client
        self._value = _value

    @classmethod
    def from_mcp_resource(
        cls, client: Client, mcp_resource: mcp.types.Resource
    ) -> ProxyResource:
        """Factory method to create a ProxyResource from a raw MCP resource schema."""
        return cls(
            client=client,
            uri=mcp_resource.uri,
            name=mcp_resource.name,
            description=mcp_resource.description,
            mime_type=mcp_resource.mimeType or "text/plain",
        )

    async def read(self) -> str | bytes:
        """Read the resource content from the remote server."""
        if self._value is not None:
            return self._value

        async with self._client:
            result = await self._client.read_resource(self.uri)
        if isinstance(result[0], TextResourceContents):
            return result[0].text
        elif isinstance(result[0], BlobResourceContents):
            return result[0].blob
        else:
            raise ResourceError(f"Unsupported content type: {type(result[0])}")


class ProxyTemplate(ResourceTemplate):
    """
    A ResourceTemplate that represents and creates resources from a remote server template.
    """

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self._client = client

    @classmethod
    def from_mcp_template(
        cls, client: Client, mcp_template: mcp.types.ResourceTemplate
    ) -> ProxyTemplate:
        """Factory method to create a ProxyTemplate from a raw MCP template schema."""
        return cls(
            client=client,
            uri_template=mcp_template.uriTemplate,
            name=mcp_template.name,
            description=mcp_template.description,
            mime_type=mcp_template.mimeType or "text/plain",
            parameters={},  # Remote templates don't have local parameters
        )

    async def create_resource(
        self,
        uri: str,
        params: dict[str, Any],
        context: Context | None = None,
    ) -> ProxyResource:
        """Create a resource from the template by calling the remote server."""
        # don't use the provided uri, because it may not be the same as the
        # uri_template on the remote server.
        # quote params to ensure they are valid for the uri_template
        parameterized_uri = self.uri_template.format(
            **{k: quote(v, safe="") for k, v in params.items()}
        )
        async with self._client:
            result = await self._client.read_resource(parameterized_uri)

        if isinstance(result[0], TextResourceContents):
            value = result[0].text
        elif isinstance(result[0], BlobResourceContents):
            value = result[0].blob
        else:
            raise ResourceError(f"Unsupported content type: {type(result[0])}")

        return ProxyResource(
            client=self._client,
            uri=parameterized_uri,
            name=self.name,
            description=self.description,
            mime_type=result[0].mimeType,
            _value=value,
        )


class ProxyPrompt(Prompt):
    """
    A Prompt that represents and renders a prompt from a remote server.
    """

    _client: Client

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self._client = client

    @classmethod
    def from_mcp_prompt(
        cls, client: Client, mcp_prompt: mcp.types.Prompt
    ) -> ProxyPrompt:
        """Factory method to create a ProxyPrompt from a raw MCP prompt schema."""
        arguments = [
            PromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required or False,
            )
            for arg in mcp_prompt.arguments or []
        ]
        return cls(
            client=client,
            name=mcp_prompt.name,
            description=mcp_prompt.description,
            arguments=arguments,
        )

    async def render(self, arguments: dict[str, Any]) -> list[PromptMessage]:
        """Render the prompt by making a call through the client."""
        async with self._client:
            result = await self._client.get_prompt(self.name, arguments)
        return result.messages


class FastMCPProxy(FastMCP):
    """
    A FastMCP server that acts as a proxy to a remote MCP-compliant server.
    It uses specialized managers that fulfill requests via an HTTP client.
    """

    def __init__(self, client: Client, **kwargs):
        """
        Initializes the proxy server.

        Args:
            client: The FastMCP client connected to the backend server.
            **kwargs: Additional settings for the FastMCP server.
        """
        super().__init__(**kwargs)
        self.client = client

        # Replace the default managers with our specialized proxy managers.
        self._tool_manager = ProxyToolManager(client=self.client)
        self._resource_manager = ProxyResourceManager(client=self.client)
        self._prompt_manager = ProxyPromptManager(client=self.client)
