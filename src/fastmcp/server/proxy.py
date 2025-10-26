from __future__ import annotations

import inspect
import warnings
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import mcp.types
from mcp import ServerSession
from mcp.client.session import ClientSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.shared.exceptions import McpError
from mcp.types import (
    METHOD_NOT_FOUND,
    BlobResourceContents,
    GetPromptResult,
    TextResourceContents,
)
from pydantic.networks import AnyUrl

import fastmcp
from fastmcp.client.client import Client, FastMCP1Server
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.logging import LogMessage
from fastmcp.client.roots import RootsList
from fastmcp.client.transports import ClientTransportT
from fastmcp.exceptions import NotFoundError, ResourceError, ToolError
from fastmcp.mcp_config import MCPConfig
from fastmcp.prompts import Prompt, PromptMessage
from fastmcp.prompts.prompt import PromptArgument
from fastmcp.prompts.prompt_manager import PromptManager
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.resources.resource_manager import ResourceManager
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_context
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.tools.tool_manager import ToolManager
from fastmcp.tools.tool_transform import (
    apply_transformations_to_tools,
)
from fastmcp.utilities.components import MirroredComponent
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server import Context

logger = get_logger(__name__)

# Type alias for client factory functions
ClientFactoryT = Callable[[], Client] | Callable[[], Awaitable[Client]]


class ProxyManagerMixin:
    """A mixin for proxy managers to provide a unified client retrieval method."""

    client_factory: ClientFactoryT

    async def _get_client(self) -> Client:
        """Gets a client instance by calling the sync or async factory."""
        client = self.client_factory()
        if inspect.isawaitable(client):
            client = await client
        return client


class ProxyToolManager(ToolManager, ProxyManagerMixin):
    """A ToolManager that sources its tools from a remote client in addition to local and mounted tools."""

    def __init__(self, client_factory: ClientFactoryT, **kwargs: Any):
        super().__init__(**kwargs)
        self.client_factory = client_factory

    async def get_tools(self) -> dict[str, Tool]:
        """Gets the unfiltered tool inventory including local, mounted, and proxy tools."""
        # First get local and mounted tools from parent
        all_tools = await super().get_tools()

        # Then add proxy tools, but don't overwrite existing ones
        try:
            client = await self._get_client()
            async with client:
                client_tools = await client.list_tools()
                for tool in client_tools:
                    if tool.name not in all_tools:
                        all_tools[tool.name] = ProxyTool.from_mcp_tool(client, tool)
        except McpError as e:
            if e.error.code == METHOD_NOT_FOUND:
                pass  # No tools available from proxy
            else:
                raise e

        transformed_tools = apply_transformations_to_tools(
            tools=all_tools,
            transformations=self.transformations,
        )

        return transformed_tools

    async def list_tools(self) -> list[Tool]:
        """Gets the filtered list of tools including local, mounted, and proxy tools."""
        tools_dict = await self.get_tools()
        return list(tools_dict.values())

    async def call_tool(self, key: str, arguments: dict[str, Any]) -> ToolResult:
        """Calls a tool, trying local/mounted first, then proxy if not found."""
        try:
            # First try local and mounted tools
            return await super().call_tool(key, arguments)
        except NotFoundError:
            # If not found locally, try proxy
            client = await self._get_client()
            async with client:
                result = await client.call_tool(key, arguments)
                return ToolResult(
                    content=result.content,
                    structured_content=result.structured_content,
                )


class ProxyResourceManager(ResourceManager, ProxyManagerMixin):
    """A ResourceManager that sources its resources from a remote client in addition to local and mounted resources."""

    def __init__(self, client_factory: ClientFactoryT, **kwargs: Any):
        super().__init__(**kwargs)
        self.client_factory = client_factory

    async def get_resources(self) -> dict[str, Resource]:
        """Gets the unfiltered resource inventory including local, mounted, and proxy resources."""
        # First get local and mounted resources from parent
        all_resources = await super().get_resources()

        # Then add proxy resources, but don't overwrite existing ones
        try:
            client = await self._get_client()
            async with client:
                client_resources = await client.list_resources()
                for resource in client_resources:
                    if str(resource.uri) not in all_resources:
                        all_resources[str(resource.uri)] = (
                            ProxyResource.from_mcp_resource(client, resource)
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
            client = await self._get_client()
            async with client:
                client_templates = await client.list_resource_templates()
                for template in client_templates:
                    if template.uriTemplate not in all_templates:
                        all_templates[template.uriTemplate] = (
                            ProxyTemplate.from_mcp_template(client, template)
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
            client = await self._get_client()
            async with client:
                result = await client.read_resource(uri)
                if isinstance(result[0], TextResourceContents):
                    return result[0].text
                elif isinstance(result[0], BlobResourceContents):
                    return result[0].blob
                else:
                    raise ResourceError(
                        f"Unsupported content type: {type(result[0])}"
                    ) from None


class ProxyPromptManager(PromptManager, ProxyManagerMixin):
    """A PromptManager that sources its prompts from a remote client in addition to local and mounted prompts."""

    def __init__(self, client_factory: ClientFactoryT, **kwargs: Any):
        super().__init__(**kwargs)
        self.client_factory = client_factory

    async def get_prompts(self) -> dict[str, Prompt]:
        """Gets the unfiltered prompt inventory including local, mounted, and proxy prompts."""
        # First get local and mounted prompts from parent
        all_prompts = await super().get_prompts()

        # Then add proxy prompts, but don't overwrite existing ones
        try:
            client = await self._get_client()
            async with client:
                client_prompts = await client.list_prompts()
                for prompt in client_prompts:
                    if prompt.name not in all_prompts:
                        all_prompts[prompt.name] = ProxyPrompt.from_mcp_prompt(
                            client, prompt
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
            client = await self._get_client()
            async with client:
                result = await client.get_prompt(name, arguments)
                return result


class ProxyTool(Tool, MirroredComponent):
    """
    A Tool that represents and executes a tool on a remote server.
    """

    def __init__(self, client: Client, **kwargs: Any):
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
            output_schema=mcp_tool.outputSchema,
            meta=mcp_tool.meta,
            tags=(mcp_tool.meta or {}).get("_fastmcp", {}).get("tags", []),
            _mirrored=True,
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> ToolResult:
        """Executes the tool by making a call through the client."""
        async with self._client:
            result = await self._client.call_tool_mcp(
                name=self.name,
                arguments=arguments,
            )
        if result.isError:
            raise ToolError(cast(mcp.types.TextContent, result.content[0]).text)
        return ToolResult(
            content=result.content,
            structured_content=result.structuredContent,
        )


class ProxyResource(Resource, MirroredComponent):
    """
    A Resource that represents and reads a resource from a remote server.
    """

    _client: Client
    _value: str | bytes | None = None

    def __init__(
        self,
        client: Client,
        *,
        _value: str | bytes | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._client = client
        self._value = _value

    @classmethod
    def from_mcp_resource(
        cls,
        client: Client,
        mcp_resource: mcp.types.Resource,
    ) -> ProxyResource:
        """Factory method to create a ProxyResource from a raw MCP resource schema."""

        return cls(
            client=client,
            uri=mcp_resource.uri,
            name=mcp_resource.name,
            description=mcp_resource.description,
            mime_type=mcp_resource.mimeType or "text/plain",
            meta=mcp_resource.meta,
            tags=(mcp_resource.meta or {}).get("_fastmcp", {}).get("tags", []),
            _mirrored=True,
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


class ProxyTemplate(ResourceTemplate, MirroredComponent):
    """
    A ResourceTemplate that represents and creates resources from a remote server template.
    """

    def __init__(self, client: Client, **kwargs: Any):
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
            meta=mcp_template.meta,
            tags=(mcp_template.meta or {}).get("_fastmcp", {}).get("tags", []),
            _mirrored=True,
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
            meta=self.meta,
            tags=(self.meta or {}).get("_fastmcp", {}).get("tags", []),
            _value=value,
        )


class ProxyPrompt(Prompt, MirroredComponent):
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
            meta=mcp_prompt.meta,
            tags=(mcp_prompt.meta or {}).get("_fastmcp", {}).get("tags", []),
            _mirrored=True,
        )

    async def render(self, arguments: dict[str, Any]) -> list[PromptMessage]:
        """Render the prompt by making a call through the client."""
        async with self._client:
            result = await self._client.get_prompt(self.name, arguments)
        return result.messages


class FastMCPProxy(FastMCP):
    """
    A FastMCP server that acts as a proxy to a remote MCP-compliant server.
    It uses specialized managers that fulfill requests via a client factory.
    """

    def __init__(
        self,
        client: Client | None = None,
        *,
        client_factory: ClientFactoryT | None = None,
        **kwargs,
    ):
        """
        Initializes the proxy server.

        FastMCPProxy requires explicit session management via client_factory.
        Use FastMCP.as_proxy() for convenience with automatic session strategy.

        Args:
            client: [DEPRECATED] A Client instance. Use client_factory instead for explicit
                   session management. When provided, a client_factory will be automatically
                   created that provides session isolation for backwards compatibility.
            client_factory: A callable that returns a Client instance when called.
                           This gives you full control over session creation and reuse.
                           Can be either a synchronous or asynchronous function.
            **kwargs: Additional settings for the FastMCP server.
        """

        super().__init__(**kwargs)

        # Handle client and client_factory parameters
        if client is not None and client_factory is not None:
            raise ValueError("Cannot specify both 'client' and 'client_factory'")

        if client is not None:
            # Deprecated in 2.10.3
            if fastmcp.settings.deprecation_warnings:
                warnings.warn(
                    "Passing 'client' to FastMCPProxy is deprecated. Use 'client_factory' instead for explicit session management. "
                    "For automatic session strategy, use FastMCP.as_proxy().",
                    DeprecationWarning,
                    stacklevel=2,
                )

            # Create a factory that provides session isolation for backwards compatibility
            def deprecated_client_factory():
                return client.new()

            self.client_factory = deprecated_client_factory
        elif client_factory is not None:
            self.client_factory = client_factory
        else:
            raise ValueError("Must specify 'client_factory'")

        # Replace the default managers with our specialized proxy managers.
        self._tool_manager = ProxyToolManager(
            client_factory=self.client_factory,
            # Propagate the transformations from the base class tool manager
            transformations=self._tool_manager.transformations,
        )
        self._resource_manager = ProxyResourceManager(
            client_factory=self.client_factory
        )
        self._prompt_manager = ProxyPromptManager(client_factory=self.client_factory)


async def default_proxy_roots_handler(
    context: RequestContext[ClientSession, LifespanContextT],
) -> RootsList:
    """
    A handler that forwards the list roots request from the remote server to the proxy's connected clients and relays the response back to the remote server.
    """
    ctx = get_context()
    return await ctx.list_roots()


class ProxyClient(Client[ClientTransportT]):
    """
    A proxy client that forwards advanced interactions between a remote MCP server and the proxy's connected clients.
    Supports forwarding roots, sampling, elicitation, logging, and progress.
    """

    def __init__(
        self,
        transport: ClientTransportT
        | FastMCP
        | FastMCP1Server
        | AnyUrl
        | Path
        | MCPConfig
        | dict[str, Any]
        | str,
        **kwargs,
    ):
        if "name" not in kwargs:
            kwargs["name"] = self.generate_name()
        if "roots" not in kwargs:
            kwargs["roots"] = default_proxy_roots_handler
        if "sampling_handler" not in kwargs:
            kwargs["sampling_handler"] = ProxyClient.default_sampling_handler
        if "elicitation_handler" not in kwargs:
            kwargs["elicitation_handler"] = ProxyClient.default_elicitation_handler
        if "log_handler" not in kwargs:
            kwargs["log_handler"] = ProxyClient.default_log_handler
        if "progress_handler" not in kwargs:
            kwargs["progress_handler"] = ProxyClient.default_progress_handler
        super().__init__(**kwargs | {"transport": transport})

    @classmethod
    async def default_sampling_handler(
        cls,
        messages: list[mcp.types.SamplingMessage],
        params: mcp.types.CreateMessageRequestParams,
        context: RequestContext[ClientSession, LifespanContextT],
    ) -> mcp.types.CreateMessageResult:
        """
        A handler that forwards the sampling request from the remote server to the proxy's connected clients and relays the response back to the remote server.
        """
        ctx = get_context()
        content = await ctx.sample(
            list(messages),
            system_prompt=params.systemPrompt,
            temperature=params.temperature,
            max_tokens=params.maxTokens,
            model_preferences=params.modelPreferences,
        )
        if isinstance(content, mcp.types.ResourceLink | mcp.types.EmbeddedResource):
            raise RuntimeError("Content is not supported")
        return mcp.types.CreateMessageResult(
            role="assistant",
            model="fastmcp-client",
            content=content,
        )

    @classmethod
    async def default_elicitation_handler(
        cls,
        message: str,
        response_type: type,
        params: mcp.types.ElicitRequestParams,
        context: RequestContext[ClientSession, LifespanContextT],
    ) -> ElicitResult:
        """
        A handler that forwards the elicitation request from the remote server to the proxy's connected clients and relays the response back to the remote server.
        """
        ctx = get_context()
        result = await ctx.session.elicit(
            message=message,
            requestedSchema=params.requestedSchema,
            related_request_id=ctx.request_id,
        )
        return ElicitResult(action=result.action, content=result.content)

    @classmethod
    async def default_log_handler(cls, message: LogMessage) -> None:
        """
        A handler that forwards the log notification from the remote server to the proxy's connected clients.
        """
        ctx = get_context()
        msg = message.data.get("msg")
        extra = message.data.get("extra")
        await ctx.log(msg, level=message.level, logger_name=message.logger, extra=extra)

    @classmethod
    async def default_progress_handler(
        cls,
        progress: float,
        total: float | None,
        message: str | None,
    ) -> None:
        """
        A handler that forwards the progress notification from the remote server to the proxy's connected clients.
        """
        ctx = get_context()
        await ctx.report_progress(progress, total, message)


class StatefulProxyClient(ProxyClient[ClientTransportT]):
    """
    A proxy client that provides a stateful client factory for the proxy server.

    The stateful proxy client bound its copy to the server session.
    And it will be disconnected when the session is exited.

    This is useful to proxy a stateful mcp server such as the Playwright MCP server.
    Note that it is essential to ensure that the proxy server itself is also stateful.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._caches: dict[ServerSession, Client[ClientTransportT]] = {}

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        """
        The stateful proxy client will be forced disconnected when the session is exited.
        So we do nothing here.
        """

    async def clear(self):
        """
        Clear all cached clients and force disconnect them.
        """
        while self._caches:
            _, cache = self._caches.popitem()
            await cache._disconnect(force=True)

    def new_stateful(self) -> Client[ClientTransportT]:
        """
        Create a new stateful proxy client instance with the same configuration.

        Use this method as the client factory for stateful proxy server.
        """
        session = get_context().session
        proxy_client = self._caches.get(session, None)

        if proxy_client is None:
            proxy_client = self.new()
            logger.debug(f"{proxy_client} created for {session}")
            self._caches[session] = proxy_client

            async def _on_session_exit():
                self._caches.pop(session)
                logger.debug(f"{proxy_client} will be disconnect")
                await proxy_client._disconnect(force=True)

            session._exit_stack.push_async_callback(_on_session_exit)

        return proxy_client
