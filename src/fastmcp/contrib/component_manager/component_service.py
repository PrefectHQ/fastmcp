"""
ComponentService: Provides async management of tools, resources, and prompts for FastMCP servers.
Handles enabling/disabling components both locally and across mounted servers.
"""

from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers import FastMCPProvider, Provider
from fastmcp.server.server import FastMCP
from fastmcp.server.transforms import Namespace
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def _reverse_through_transforms(
    provider: Provider,
    key: str,
    component_type: str,
) -> str | None:
    """Reverse a key through provider's transforms.

    Iterates through transforms in reverse order (outer to inner) and
    reverses the key transformation.

    Args:
        provider: The provider with transforms.
        key: The transformed key.
        component_type: Either "tool", "prompt", or "resource".

    Returns:
        The original key if transformations can be reversed, None otherwise.
    """
    current_key = key
    # Iterate transforms in reverse (outer first)
    for transform in reversed(provider._transforms):
        if isinstance(transform, Namespace):
            # Namespace transform - try to reverse
            if component_type in ("tool", "prompt"):
                original = transform._reverse_name(current_key)
            else:
                original = transform._reverse_uri(current_key)
            if original is None:
                return None
            current_key = original
        # Other transform types don't transform keys in ways we need to reverse
        # for enable/disable operations (ToolTransform renames tools but
        # the original name is what we need)
    return current_key


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
    if isinstance(provider, FastMCPProvider):
        # FastMCPProvider with layers - reverse through layers
        if provider._transforms:
            original = _reverse_through_transforms(provider, key, component_type)
            if original is not None:
                return provider.server, original
        else:
            # Direct FastMCPProvider - no transformation
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
            The tool that was enabled (highest version)
        """
        logger.debug("Enabling tool: %s", name)

        # 1. Check local tools first - find ALL versions of this tool
        # Keys are "tool:name@" (unversioned) or "tool:name@version" (versioned)
        key_prefix = f"{Tool.make_key(name)}@"
        matching_keys = [
            k
            for k in self._server._local_provider._components
            if k == key_prefix or k.startswith(key_prefix)
        ]
        if matching_keys:
            self._server.enable(keys=matching_keys)
            tool = await self._server.get_tool(name)
            if tool is None:
                raise NotFoundError(f"Unknown tool: {name!r}")
            return tool

        # 2. Check mounted servers via FastMCPProvider
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
            The tool that was disabled (highest version)
        """
        logger.debug("Disabling tool: %s", name)

        # 1. Check local tools first - find ALL versions of this tool
        # Keys are "tool:name@" (unversioned) or "tool:name@version" (versioned)
        key_prefix = f"{Tool.make_key(name)}@"
        matching_keys = [
            k
            for k in self._server._local_provider._components
            if k == key_prefix or k.startswith(key_prefix)
        ]
        if matching_keys:
            # Get the highest version tool to return
            tool = await self._server.get_tool(name)
            if tool is None or not isinstance(tool, Tool):
                raise NotFoundError(f"Unknown tool: {name!r}")
            self._server.disable(keys=matching_keys)
            return tool

        # 2. Check mounted servers via FastMCPProvider
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
            The resource that was enabled (highest version)
        """
        logger.debug("Enabling resource: %s", uri)

        # 1. Check local components first - find ALL versions
        # Keys are "resource:uri@" or "resource:uri@version" (and same for template)
        resource_prefix = f"{Resource.make_key(uri)}@"
        template_prefix = f"{ResourceTemplate.make_key(uri)}@"
        resource_keys = [
            k
            for k in self._server._local_provider._components
            if k == resource_prefix or k.startswith(resource_prefix)
        ]
        template_keys = [
            k
            for k in self._server._local_provider._components
            if k == template_prefix or k.startswith(template_prefix)
        ]
        if resource_keys:
            self._server.enable(keys=resource_keys)
            resource = await self._server.get_resource(uri)
            return resource
        if template_keys:
            self._server.enable(keys=template_keys)
            template = await self._server.get_resource_template(uri)
            return template

        # 2. Check mounted servers via FastMCPProvider
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
            The resource that was disabled (highest version)
        """
        logger.debug("Disabling resource: %s", uri)

        # 1. Check local components first - find ALL versions
        # Keys are "resource:uri@" or "resource:uri@version" (and same for template)
        resource_prefix = f"{Resource.make_key(uri)}@"
        template_prefix = f"{ResourceTemplate.make_key(uri)}@"
        resource_keys = [
            k
            for k in self._server._local_provider._components
            if k == resource_prefix or k.startswith(resource_prefix)
        ]
        template_keys = [
            k
            for k in self._server._local_provider._components
            if k == template_prefix or k.startswith(template_prefix)
        ]
        if resource_keys:
            # Get the highest version to return before disabling
            resource = await self._server.get_resource(uri)
            self._server.disable(keys=resource_keys)
            return resource
        if template_keys:
            # Get the highest version to return before disabling
            template = await self._server.get_resource_template(uri)
            self._server.disable(keys=template_keys)
            return template

        # 2. Check mounted servers via FastMCPProvider
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
            The prompt that was enabled (highest version)
        """
        logger.debug("Enabling prompt: %s", name)

        # 1. Check local prompts first - find ALL versions of this prompt
        # Keys are "prompt:name@" (unversioned) or "prompt:name@version" (versioned)
        key_prefix = f"{Prompt.make_key(name)}@"
        matching_keys = [
            k
            for k in self._server._local_provider._components
            if k == key_prefix or k.startswith(key_prefix)
        ]
        if matching_keys:
            self._server.enable(keys=matching_keys)
            prompt = await self._server.get_prompt(name)
            if prompt is None:
                raise NotFoundError(f"Unknown prompt: {name}")
            return prompt

        # 2. Check mounted servers via FastMCPProvider
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
            The prompt that was disabled (highest version)
        """
        logger.debug("Disabling prompt: %s", name)

        # 1. Check local prompts first - find ALL versions of this prompt
        # Keys are "prompt:name@" (unversioned) or "prompt:name@version" (versioned)
        key_prefix = f"{Prompt.make_key(name)}@"
        matching_keys = [
            k
            for k in self._server._local_provider._components
            if k == key_prefix or k.startswith(key_prefix)
        ]
        if matching_keys:
            # Get the highest version prompt to return
            prompt = await self._server.get_prompt(name)
            if prompt is None or not isinstance(prompt, Prompt):
                raise NotFoundError(f"Unknown prompt: {name}")
            self._server.disable(keys=matching_keys)
            return prompt

        # 2. Check mounted servers via FastMCPProvider
        for provider in self._server._providers:
            result = _get_mounted_server_and_key(provider, name, "prompt")
            if result is not None:
                server, unprefixed = result
                mounted_service = ComponentService(server)
                prompt = await mounted_service._disable_prompt(unprefixed)
                return prompt
        raise NotFoundError(f"Unknown prompt: {name}")
