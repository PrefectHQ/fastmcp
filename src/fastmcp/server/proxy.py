from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import mcp.types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from mcp.types import (
    METHOD_NOT_FOUND,
    BlobResourceContents,
    EmbeddedResource,
    GetPromptResult,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic.networks import AnyUrl

from fastmcp.client import Client
from fastmcp.exceptions import NotFoundError, ResourceError, ToolError
from fastmcp.prompts import Prompt, PromptMessage
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.context import Context
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server import Context

logger = get_logger(__name__)


class ProxyTool(Tool):
    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self._client = client

    @classmethod
    async def from_client(cls, client: Client, tool: mcp.types.Tool) -> ProxyTool:
        return cls(
            client=client,
            name=tool.name,
            description=tool.description,
            parameters=tool.inputSchema,
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        # the client context manager will swallow any exceptions inside a TaskGroup
        # so we return the raw result and raise an exception ourselves
        async with self._client:
            result = await self._client.call_tool_mcp(
                name=self.name,
                arguments=arguments,
            )
        if result.isError:
            raise ToolError(cast(mcp.types.TextContent, result.content[0]).text)
        return result.content


class ProxyResource(Resource):
    _client: Client
    _value: str | bytes | None = None

    def __init__(self, client: Client, *, _value: str | bytes | None = None, **kwargs):
        super().__init__(**kwargs)
        self._client = client
        self._value = _value

    @classmethod
    async def from_client(
        cls, client: Client, resource: mcp.types.Resource
    ) -> ProxyResource:
        return cls(
            client=client,
            uri=resource.uri,
            name=resource.name,
            description=resource.description,
            mime_type=resource.mimeType,
        )

    async def read(self) -> str | bytes:
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
    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self._client = client

    @classmethod
    async def from_client(
        cls, client: Client, template: mcp.types.ResourceTemplate
    ) -> ProxyTemplate:
        return cls(
            client=client,
            uri_template=template.uriTemplate,
            name=template.name,
            description=template.description,
            parameters={},
        )

    async def create_resource(
        self,
        uri: str,
        params: dict[str, Any],
        context: Context | None = None,
    ) -> ProxyResource:
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
    _client: Client

    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self._client = client

    @classmethod
    async def from_client(cls, client: Client, prompt: mcp.types.Prompt) -> ProxyPrompt:
        return cls(
            client=client,
            name=prompt.name,
            description=prompt.description,
            arguments=[a.model_dump() for a in prompt.arguments or []],
        )

    async def render(self, arguments: dict[str, Any]) -> list[PromptMessage]:
        async with self._client:
            result = await self._client.get_prompt(self.name, arguments)
        return result.messages


class FastMCPProxy(FastMCP):
    def __init__(self, client: Client, **kwargs):
        super().__init__(**kwargs)
        self.client = client

    async def get_tools(self) -> dict[str, Tool]:
        tools = await super().get_tools()

        async with self.client:
            try:
                client_tools = await self.client.list_tools()
            except McpError as e:
                if e.error.code == METHOD_NOT_FOUND:
                    client_tools = []
                else:
                    raise e
            for tool in client_tools:
                # don't overwrite tools defined in the server
                if tool.name not in tools:
                    tool_proxy = await ProxyTool.from_client(self.client, tool)
                    tools[tool_proxy.name] = tool_proxy

        return tools

    async def get_resources(self) -> dict[str, Resource]:
        resources = await super().get_resources()

        async with self.client:
            try:
                client_resources = await self.client.list_resources()
            except McpError as e:
                if e.error.code == METHOD_NOT_FOUND:
                    client_resources = []
                else:
                    raise e
            for resource in client_resources:
                # don't overwrite resources defined in the server
                if str(resource.uri) not in resources:
                    resource_proxy = await ProxyResource.from_client(
                        self.client, resource
                    )
                    resources[str(resource_proxy.uri)] = resource_proxy

        return resources

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        templates = await super().get_resource_templates()

        async with self.client:
            try:
                client_templates = await self.client.list_resource_templates()
            except McpError as e:
                if e.error.code == METHOD_NOT_FOUND:
                    client_templates = []
                else:
                    raise e
            for template in client_templates:
                # don't overwrite templates defined in the server
                if template.uriTemplate not in templates:
                    template_proxy = await ProxyTemplate.from_client(
                        self.client, template
                    )
                    templates[template_proxy.uri_template] = template_proxy

        return templates

    async def get_prompts(self) -> dict[str, Prompt]:
        prompts = await super().get_prompts()

        async with self.client:
            try:
                client_prompts = await self.client.list_prompts()
            except McpError as e:
                if e.error.code == METHOD_NOT_FOUND:
                    client_prompts = []
                else:
                    raise e
            for prompt in client_prompts:
                # don't overwrite prompts defined in the server
                if prompt.name not in prompts:
                    prompt_proxy = await ProxyPrompt.from_client(self.client, prompt)
                    prompts[prompt_proxy.name] = prompt_proxy

        return prompts

    async def _call_tool(
        self, key: str, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        try:
            result = await super()._call_tool(key, arguments)
            return result
        except NotFoundError:
            async with self.client:
                result = await self.client.call_tool(key, arguments)
            return result

    async def _read_resource(self, uri: AnyUrl | str) -> list[ReadResourceContents]:
        try:
            result = await super()._read_resource(uri)
            return result
        except NotFoundError:
            async with self.client:
                resource = await self.client.read_resource(uri)
                if isinstance(resource[0], TextResourceContents):
                    content = resource[0].text
                elif isinstance(resource[0], BlobResourceContents):
                    content = resource[0].blob
                else:
                    raise ValueError(f"Unsupported content type: {type(resource[0])}")

            return [
                ReadResourceContents(content=content, mime_type=resource[0].mimeType)
            ]

    async def _get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        try:
            result = await super()._get_prompt(name, arguments)
            return result
        except NotFoundError:
            async with self.client:
                result = await self.client.get_prompt(name, arguments)
            return result
