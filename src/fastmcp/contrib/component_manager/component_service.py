"""
ComponentService: Provides async management of tools, resources, and prompts for FastMCP servers.
Handles enabling/disabling components both locally and across mounted servers.
"""

from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.server import FastMCP, has_resource_prefix, remove_resource_prefix
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class ComponentService:
    """Service for managing components like tools, resources, and prompts."""

    def __init__(self, server: FastMCP):
        self._server = server
        self._tool_manager = server._tool_manager
        self._resource_manager = server._resource_manager
        self._prompt_manager = server._prompt_manager

    async def _enable_tool(self, key: str) -> Tool:
        """Handle 'enableTool' requests.

        Args:
            key: The key of the tool to enable

        Returns:
            The tool that was enabled
        """
        logger.debug("Enabling tool: %s", key)

        # 1. Check local tools first. The server will have already applied its filter.
        if key in self._server._tool_manager._tools:
            tool: Tool = await self._server.get_tool(key)
            tool.enable()
            return tool

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._server._mounted_servers):
            if mounted.prefix:
                if key.startswith(f"{mounted.prefix}_"):
                    tool_key = key.removeprefix(f"{mounted.prefix}_")
                    mounted_service = ComponentService(mounted.server)
                    tool = await mounted_service._enable_tool(tool_key)
                    return tool
                else:
                    continue
        raise NotFoundError(f"Unknown tool: {key}")

    async def _disable_tool(self, key: str) -> Tool:
        """Handle 'disableTool' requests.

        Args:
            key: The key of the tool to disable

        Returns:
            The tool that was disabled
        """
        logger.debug("Disable tool: %s", key)

        # 1. Check local tools first. The server will have already applied its filter.
        if key in self._server._tool_manager._tools:
            tool: Tool = await self._server.get_tool(key)
            tool.disable()
            return tool

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._server._mounted_servers):
            if mounted.prefix:
                if key.startswith(f"{mounted.prefix}_"):
                    tool_key = key.removeprefix(f"{mounted.prefix}_")
                    mounted_service = ComponentService(mounted.server)
                    tool = await mounted_service._disable_tool(tool_key)
                    return tool
                else:
                    continue
        raise NotFoundError(f"Unknown tool: {key}")

    async def _enable_resource(self, key: str) -> Resource | ResourceTemplate:
        """Handle 'enableResource' requests.

        Args:
            key: The key of the resource to enable

        Returns:
            The resource that was enabled
        """
        logger.debug("Enabling resource: %s", key)

        # 1. Check local resources first. The server will have already applied its filter.
        if key in self._resource_manager._resources:
            resource: Resource = await self._server.get_resource(key)
            resource.enable()
            return resource
        if key in self._resource_manager._templates:
            template: ResourceTemplate = await self._server.get_resource_template(key)
            template.enable()
            return template

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._server._mounted_servers):
            if mounted.prefix:
                if has_resource_prefix(
                    key,
                    mounted.prefix,
                    mounted.resource_prefix_format,
                ):
                    key = remove_resource_prefix(
                        key,
                        mounted.prefix,
                        mounted.resource_prefix_format,
                    )
                    mounted_service = ComponentService(mounted.server)
                    mounted_resource: (
                        Resource | ResourceTemplate
                    ) = await mounted_service._enable_resource(key)
                    return mounted_resource
            else:
                continue
        raise NotFoundError(f"Unknown resource: {key}")

    async def _disable_resource(self, key: str) -> Resource | ResourceTemplate:
        """Handle 'disableResource' requests.

        Args:
            key: The key of the resource to disable

        Returns:
            The resource that was disabled
        """
        logger.debug("Disable resource: %s", key)

        # 1. Check local resources first. The server will have already applied its filter.
        if key in self._resource_manager._resources:
            resource: Resource = await self._server.get_resource(key)
            resource.disable()
            return resource
        if key in self._resource_manager._templates:
            template: ResourceTemplate = await self._server.get_resource_template(key)
            template.disable()
            return template

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._server._mounted_servers):
            if mounted.prefix:
                if has_resource_prefix(
                    key,
                    mounted.prefix,
                    mounted.resource_prefix_format,
                ):
                    key = remove_resource_prefix(
                        key,
                        mounted.prefix,
                        mounted.resource_prefix_format,
                    )
                    mounted_service = ComponentService(mounted.server)
                    mounted_resource: (
                        Resource | ResourceTemplate
                    ) = await mounted_service._disable_resource(key)
                    return mounted_resource
            else:
                continue
        raise NotFoundError(f"Unknown resource: {key}")

    async def _enable_prompt(self, key: str) -> Prompt:
        """Handle 'enablePrompt' requests.

        Args:
            key: The key of the prompt to enable

        Returns:
            The prompt that was enabled
        """
        logger.debug("Enabling prompt: %s", key)

        # 1. Check local prompts first. The server will have already applied its filter.
        if key in self._server._prompt_manager._prompts:
            prompt: Prompt = await self._server.get_prompt(key)
            prompt.enable()
            return prompt

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._server._mounted_servers):
            if mounted.prefix:
                if key.startswith(f"{mounted.prefix}_"):
                    prompt_key = key.removeprefix(f"{mounted.prefix}_")
                    mounted_service = ComponentService(mounted.server)
                    prompt = await mounted_service._enable_prompt(prompt_key)
                    return prompt
                else:
                    continue
        raise NotFoundError(f"Unknown prompt: {key}")

    async def _disable_prompt(self, key: str) -> Prompt:
        """Handle 'disablePrompt' requests.

        Args:
            key: The key of the prompt to disable

        Returns:
            The prompt that was disabled
        """

        # 1. Check local prompts first. The server will have already applied its filter.
        if key in self._server._prompt_manager._prompts:
            prompt: Prompt = await self._server.get_prompt(key)
            prompt.disable()
            return prompt

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._server._mounted_servers):
            if mounted.prefix:
                if key.startswith(f"{mounted.prefix}_"):
                    prompt_key = key.removeprefix(f"{mounted.prefix}_")
                    mounted_service = ComponentService(mounted.server)
                    prompt = await mounted_service._disable_prompt(prompt_key)
                    return prompt
                else:
                    continue
        raise NotFoundError(f"Unknown prompt: {key}")
