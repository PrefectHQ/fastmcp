"""
ComponentService: Provides async management of tools, resources, and prompts for FastMCP servers.
Handles enabling/disabling components both locally and across mounted servers.
"""

from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers import FastMCPProvider, Provider, TransformingProvider
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def _get_mounted_server_and_key(
    provider: Provider,
    key: str,
    component_type: str,
) -> tuple[FastMCP, str] | None:
    """Get the mounted server and unprefixed key for a component.

    Args:
        provider: The provider to check.
        key: The transformed component key.
        component_type: Either "tool", "prompt", or "resource".

    Returns:
        Tuple of (server, original_key) if the key matches this provider,
        or None if it doesn't.
    """
    if isinstance(provider, TransformingProvider):
        # TransformingProvider - reverse the transformation
        if component_type == "resource":
            original = provider._reverse_resource_uri(key)
        elif component_type == "prompt":
            original = provider._reverse_prompt_name(key)
        else:
            original = provider._reverse_tool_name(key)

        if original is not None:
            # Recursively check the wrapped provider
            return _get_mounted_server_and_key(
                provider._wrapped, original, component_type
            )
    elif isinstance(provider, FastMCPProvider):
        # Direct FastMCPProvider - no transformation, key is used directly
        return provider.server, key

    return None


class ComponentService:
    """Service for managing components like tools, resources, and prompts."""

    def __init__(self, server: FastMCP):
        self._server = server

    async def _enable_tool(self, name: str) -> Tool:
        """Handle 'enableTool' requests.

        Args:
            name: The name of the tool to enable

        Returns:
            The tool that was enabled
        """
        logger.debug("Enabling tool: %s", name)

        # 1. Check local tools first. The server will have already applied its filter.
        if Tool.make_key(name) in self._server._local_provider._components:
            self._server.enable(keys=[Tool.make_key(name)])
            tool = await self._server.get_tool(name)
            if tool is None:
                raise NotFoundError(f"Unknown tool: {name!r}")
            return tool

        # 2. Check mounted servers via FastMCPProvider/TransformingProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, name, "tool")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                tool = await mounted_service._enable_tool(unprefixed)
                return tool
        raise NotFoundError(f"Unknown tool: {name!r}")

    async def _disable_tool(self, name: str) -> Tool:
        """Handle 'disableTool' requests.

        Args:
            name: The name of the tool to disable

        Returns:
            The tool that was disabled
        """
        logger.debug("Disabling tool: %s", name)

        # 1. Check local tools first. The server will have already applied its filter.
        key = Tool.make_key(name)
        if key in self._server._local_provider._components:
            tool = self._server._local_provider._components[key]
            if not isinstance(tool, Tool):
                raise NotFoundError(f"Unknown tool: {name!r}")
            self._server.disable(keys=[key])
            return tool

        # 2. Check mounted servers via FastMCPProvider/TransformingProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, name, "tool")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                tool = await mounted_service._disable_tool(unprefixed)
                return tool
        raise NotFoundError(f"Unknown tool: {name!r}")

    async def _enable_resource(self, uri: str) -> Resource | ResourceTemplate:
        """Handle 'enableResource' requests.

        Args:
            uri: The URI of the resource to enable

        Returns:
            The resource that was enabled
        """
        logger.debug("Enabling resource: %s", uri)

        # 1. Check local components first (try resource, then template)
        resource_key = Resource.make_key(uri)
        template_key = ResourceTemplate.make_key(uri)
        component = self._server._local_provider._get_component(
            resource_key
        ) or self._server._local_provider._get_component(template_key)
        if component is not None:
            key = resource_key if isinstance(component, Resource) else template_key
            self._server.enable(keys=[key])
            return component  # type: ignore[return-value]

        # 2. Check mounted servers via FastMCPProvider/TransformingProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, uri, "resource")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                mounted_resource: (
                    Resource | ResourceTemplate
                ) = await mounted_service._enable_resource(unprefixed)
                return mounted_resource
        raise NotFoundError(f"Unknown resource: {uri}")

    async def _disable_resource(self, uri: str) -> Resource | ResourceTemplate:
        """Handle 'disableResource' requests.

        Args:
            uri: The URI of the resource to disable

        Returns:
            The resource that was disabled
        """
        logger.debug("Disabling resource: %s", uri)

        # 1. Check local components first (try resource, then template)
        resource_key = Resource.make_key(uri)
        template_key = ResourceTemplate.make_key(uri)
        component = self._server._local_provider._get_component(
            resource_key
        ) or self._server._local_provider._get_component(template_key)
        if component is not None:
            key = resource_key if isinstance(component, Resource) else template_key
            self._server.disable(keys=[key])
            return component  # type: ignore[return-value]

        # 2. Check mounted servers via FastMCPProvider/TransformingProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, uri, "resource")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                mounted_resource: (
                    Resource | ResourceTemplate
                ) = await mounted_service._disable_resource(unprefixed)
                return mounted_resource
        raise NotFoundError(f"Unknown resource: {uri}")

    async def _enable_prompt(self, name: str) -> Prompt:
        """Handle 'enablePrompt' requests.

        Args:
            name: The name of the prompt to enable

        Returns:
            The prompt that was enabled
        """
        logger.debug("Enabling prompt: %s", name)

        # 1. Check local prompts first. The server will have already applied its filter.
        key = Prompt.make_key(name)
        if key in self._server._local_provider._components:
            self._server.enable(keys=[key])
            prompt = await self._server.get_prompt(name)
            if prompt is None:
                raise NotFoundError(f"Unknown prompt: {name}")
            return prompt

        # 2. Check mounted servers via FastMCPProvider/TransformingProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, name, "prompt")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                prompt = await mounted_service._enable_prompt(unprefixed)
                return prompt
        raise NotFoundError(f"Unknown prompt: {name}")

    async def _disable_prompt(self, name: str) -> Prompt:
        """Handle 'disablePrompt' requests.

        Args:
            name: The name of the prompt to disable

        Returns:
            The prompt that was disabled
        """
        logger.debug("Disabling prompt: %s", name)

        # 1. Check local prompts first. The server will have already applied its filter.
        key = Prompt.make_key(name)
        if key in self._server._local_provider._components:
            prompt = self._server._local_provider._components[key]
            if not isinstance(prompt, Prompt):
                raise NotFoundError(f"Unknown prompt: {name}")
            self._server.disable(keys=[key])
            return prompt

        # 2. Check mounted servers via FastMCPProvider/TransformingProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, name, "prompt")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                prompt = await mounted_service._disable_prompt(unprefixed)
                return prompt
        raise NotFoundError(f"Unknown prompt: {name}")
