"""Utilities for inspecting FastMCP instances."""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, cast

import pydantic_core
from mcp.server.fastmcp import FastMCP as FastMCP1x

import fastmcp
from fastmcp import Client
from fastmcp.server.server import FastMCP


@dataclass
class ToolInfo:
    """Information about a tool."""

    key: str
    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    title: str | None = None
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None


@dataclass
class PromptInfo:
    """Information about a prompt."""

    key: str
    name: str
    description: str | None
    arguments: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    title: str | None = None
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None


@dataclass
class ResourceInfo:
    """Information about a resource."""

    key: str
    uri: str
    name: str | None
    description: str | None
    mime_type: str | None = None
    annotations: dict[str, Any] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    title: str | None = None
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None


@dataclass
class TemplateInfo:
    """Information about a resource template."""

    key: str
    uri_template: str
    name: str | None
    description: str | None
    mime_type: str | None = None
    parameters: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    title: str | None = None
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None


@dataclass
class FastMCPInfo:
    """Information extracted from a FastMCP instance."""

    name: str
    instructions: str | None
    version: str | None  # The server's own version string (if specified)
    website_url: str | None
    icons: list[dict[str, Any]] | None
    fastmcp_version: str  # Version of FastMCP generating this manifest
    mcp_version: str  # Version of MCP protocol library
    server_generation: int  # Server generation: 1 (mcp package) or 2 (fastmcp)
    tools: list[ToolInfo]
    prompts: list[PromptInfo]
    resources: list[ResourceInfo]
    templates: list[TemplateInfo]
    capabilities: dict[str, Any]


async def inspect_fastmcp_v2(mcp: FastMCP[Any]) -> FastMCPInfo:
    """Extract information from a FastMCP v2.x instance.

    Args:
        mcp: The FastMCP v2.x instance to inspect

    Returns:
        FastMCPInfo dataclass containing the extracted information
    """
    # Get all components via middleware to respect filtering and preserve metadata
    tools_list = await mcp._list_tools_middleware()
    prompts_list = await mcp._list_prompts_middleware()
    resources_list = await mcp._list_resources_middleware()
    templates_list = await mcp._list_resource_templates_middleware()

    # Extract detailed tool information
    tool_infos = []
    for tool in tools_list:
        mcp_tool = tool.to_mcp_tool(name=tool.key)
        tool_infos.append(
            ToolInfo(
                key=tool.key,
                name=tool.name or tool.key,
                description=tool.description,
                input_schema=mcp_tool.inputSchema if mcp_tool.inputSchema else {},
                output_schema=tool.output_schema,
                annotations=tool.annotations.model_dump() if tool.annotations else None,
                tags=list(tool.tags) if tool.tags else None,
                enabled=tool.enabled,
                title=tool.title,
                icons=[icon.model_dump() for icon in tool.icons]
                if tool.icons
                else None,
                meta=tool.meta,
            )
        )

    # Extract detailed prompt information
    prompt_infos = []
    for prompt in prompts_list:
        prompt_infos.append(
            PromptInfo(
                key=prompt.key,
                name=prompt.name or prompt.key,
                description=prompt.description,
                arguments=[arg.model_dump() for arg in prompt.arguments]
                if prompt.arguments
                else None,
                tags=list(prompt.tags) if prompt.tags else None,
                enabled=prompt.enabled,
                title=prompt.title,
                icons=[icon.model_dump() for icon in prompt.icons]
                if prompt.icons
                else None,
                meta=prompt.meta,
            )
        )

    # Extract detailed resource information
    resource_infos = []
    for resource in resources_list:
        resource_infos.append(
            ResourceInfo(
                key=resource.key,
                uri=resource.key,
                name=resource.name,
                description=resource.description,
                mime_type=resource.mime_type,
                annotations=resource.annotations.model_dump()
                if resource.annotations
                else None,
                tags=list(resource.tags) if resource.tags else None,
                enabled=resource.enabled,
                title=resource.title,
                icons=[icon.model_dump() for icon in resource.icons]
                if resource.icons
                else None,
                meta=resource.meta,
            )
        )

    # Extract detailed template information
    template_infos = []
    for template in templates_list:
        template_infos.append(
            TemplateInfo(
                key=template.key,
                uri_template=template.key,
                name=template.name,
                description=template.description,
                mime_type=template.mime_type,
                parameters=template.parameters,
                annotations=template.annotations.model_dump()
                if template.annotations
                else None,
                tags=list(template.tags) if template.tags else None,
                enabled=template.enabled,
                title=template.title,
                icons=[icon.model_dump() for icon in template.icons]
                if template.icons
                else None,
                meta=template.meta,
            )
        )

    # Basic MCP capabilities that FastMCP supports
    capabilities = {
        "tools": {"listChanged": True},
        "resources": {"subscribe": False, "listChanged": False},
        "prompts": {"listChanged": False},
        "logging": {},
    }

    # Extract server-level icons and website_url
    server_icons = (
        [icon.model_dump() for icon in mcp._mcp_server.icons]
        if hasattr(mcp._mcp_server, "icons") and mcp._mcp_server.icons
        else None
    )
    server_website_url = (
        mcp._mcp_server.website_url if hasattr(mcp._mcp_server, "website_url") else None
    )

    return FastMCPInfo(
        name=mcp.name,
        instructions=mcp.instructions,
        version=(mcp.version if hasattr(mcp, "version") else mcp._mcp_server.version),
        website_url=server_website_url,
        icons=server_icons,
        fastmcp_version=fastmcp.__version__,
        mcp_version=importlib.metadata.version("mcp"),
        server_generation=2,  # FastMCP v2
        tools=tool_infos,
        prompts=prompt_infos,
        resources=resource_infos,
        templates=template_infos,
        capabilities=capabilities,
    )


async def inspect_fastmcp_v1(mcp: FastMCP1x) -> FastMCPInfo:
    """Extract information from a FastMCP v1.x instance using a Client.

    Args:
        mcp: The FastMCP v1.x instance to inspect

    Returns:
        FastMCPInfo dataclass containing the extracted information
    """
    # Use a client to interact with the FastMCP1x server
    async with Client(mcp) as client:
        # Get components via client calls (these return MCP objects)
        mcp_tools = await client.list_tools()
        mcp_prompts = await client.list_prompts()
        mcp_resources = await client.list_resources()

        # Try to get resource templates (FastMCP 1.x does have templates)
        try:
            mcp_templates = await client.list_resource_templates()
        except Exception:
            mcp_templates = []

        # Extract detailed tool information from MCP Tool objects
        tool_infos = []
        for mcp_tool in mcp_tools:
            tool_infos.append(
                ToolInfo(
                    key=mcp_tool.name,
                    name=mcp_tool.name,
                    description=mcp_tool.description,
                    input_schema=mcp_tool.inputSchema if mcp_tool.inputSchema else {},
                    output_schema=None,  # v1 doesn't have output_schema
                    annotations=None,  # v1 doesn't have annotations
                    tags=None,  # v1 doesn't have tags
                    enabled=None,  # v1 doesn't have enabled field
                    title=None,  # v1 doesn't have title
                    icons=[icon.model_dump() for icon in mcp_tool.icons]
                    if hasattr(mcp_tool, "icons") and mcp_tool.icons
                    else None,
                    meta=None,  # v1 doesn't have meta field
                )
            )

        # Extract detailed prompt information from MCP Prompt objects
        prompt_infos = []
        for mcp_prompt in mcp_prompts:
            # Convert arguments if they exist
            arguments = None
            if hasattr(mcp_prompt, "arguments") and mcp_prompt.arguments:
                arguments = [arg.model_dump() for arg in mcp_prompt.arguments]

            prompt_infos.append(
                PromptInfo(
                    key=mcp_prompt.name,
                    name=mcp_prompt.name,
                    description=mcp_prompt.description,
                    arguments=arguments,
                    tags=None,  # v1 doesn't have tags
                    enabled=None,  # v1 doesn't have enabled field
                    title=None,  # v1 doesn't have title
                    icons=[icon.model_dump() for icon in mcp_prompt.icons]
                    if hasattr(mcp_prompt, "icons") and mcp_prompt.icons
                    else None,
                    meta=None,  # v1 doesn't have meta field
                )
            )

        # Extract detailed resource information from MCP Resource objects
        resource_infos = []
        for mcp_resource in mcp_resources:
            resource_infos.append(
                ResourceInfo(
                    key=str(mcp_resource.uri),
                    uri=str(mcp_resource.uri),
                    name=mcp_resource.name,
                    description=mcp_resource.description,
                    mime_type=mcp_resource.mimeType,
                    annotations=None,  # v1 doesn't have annotations
                    tags=None,  # v1 doesn't have tags
                    enabled=None,  # v1 doesn't have enabled field
                    title=None,  # v1 doesn't have title
                    icons=[icon.model_dump() for icon in mcp_resource.icons]
                    if hasattr(mcp_resource, "icons") and mcp_resource.icons
                    else None,
                    meta=None,  # v1 doesn't have meta field
                )
            )

        # Extract detailed template information from MCP ResourceTemplate objects
        template_infos = []
        for mcp_template in mcp_templates:
            template_infos.append(
                TemplateInfo(
                    key=str(mcp_template.uriTemplate),
                    uri_template=str(mcp_template.uriTemplate),
                    name=mcp_template.name,
                    description=mcp_template.description,
                    mime_type=mcp_template.mimeType,
                    parameters=None,  # v1 doesn't expose template parameters
                    annotations=None,  # v1 doesn't have annotations
                    tags=None,  # v1 doesn't have tags
                    enabled=None,  # v1 doesn't have enabled field
                    title=None,  # v1 doesn't have title
                    icons=[icon.model_dump() for icon in mcp_template.icons]
                    if hasattr(mcp_template, "icons") and mcp_template.icons
                    else None,
                    meta=None,  # v1 doesn't have meta field
                )
            )

        # Basic MCP capabilities
        capabilities = {
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
            "logging": {},
        }

        # Extract server-level icons and website_url from serverInfo
        server_info = client.initialize_result.serverInfo
        server_icons = (
            [icon.model_dump() for icon in server_info.icons]
            if hasattr(server_info, "icons") and server_info.icons
            else None
        )
        server_website_url = (
            server_info.websiteUrl if hasattr(server_info, "websiteUrl") else None
        )

        return FastMCPInfo(
            name=mcp._mcp_server.name,
            instructions=mcp._mcp_server.instructions,
            version=mcp._mcp_server.version,
            website_url=server_website_url,
            icons=server_icons,
            fastmcp_version=fastmcp.__version__,  # Version generating this manifest
            mcp_version=importlib.metadata.version("mcp"),
            server_generation=1,  # MCP v1
            tools=tool_infos,
            prompts=prompt_infos,
            resources=resource_infos,
            templates=template_infos,
            capabilities=capabilities,
        )


async def inspect_fastmcp(mcp: FastMCP[Any] | FastMCP1x) -> FastMCPInfo:
    """Extract information from a FastMCP instance into a dataclass.

    This function automatically detects whether the instance is FastMCP v1.x or v2.x
    and uses the appropriate extraction method.

    Args:
        mcp: The FastMCP instance to inspect (v1.x or v2.x)

    Returns:
        FastMCPInfo dataclass containing the extracted information
    """
    if isinstance(mcp, FastMCP1x):
        return await inspect_fastmcp_v1(mcp)
    else:
        return await inspect_fastmcp_v2(cast(FastMCP[Any], mcp))


class InspectFormat(str, Enum):
    """Output format for inspect command."""

    FASTMCP = "fastmcp"
    MCP = "mcp"


def format_fastmcp_info(info: FastMCPInfo) -> bytes:
    """Format FastMCPInfo as FastMCP-specific JSON.

    This includes FastMCP-specific fields like tags, enabled, annotations, etc.
    """
    # Build the output dict with nested structure
    result = {
        "server": {
            "name": info.name,
            "instructions": info.instructions,
            "version": info.version,
            "website_url": info.website_url,
            "icons": info.icons,
            "generation": info.server_generation,
            "capabilities": info.capabilities,
        },
        "environment": {
            "fastmcp": info.fastmcp_version,
            "mcp": info.mcp_version,
        },
        "tools": info.tools,
        "prompts": info.prompts,
        "resources": info.resources,
        "templates": info.templates,
    }

    return pydantic_core.to_json(result, indent=2)


async def format_mcp_info(mcp: FastMCP[Any] | FastMCP1x) -> bytes:
    """Format server info as standard MCP protocol JSON.

    Uses Client to get the standard MCP protocol format with camelCase fields.
    Includes version metadata at the top level.
    """
    async with Client(mcp) as client:
        # Get all the MCP protocol objects
        tools_result = await client.list_tools_mcp()
        prompts_result = await client.list_prompts_mcp()
        resources_result = await client.list_resources_mcp()
        templates_result = await client.list_resource_templates_mcp()

        # Get server info from the initialize result
        server_info = client.initialize_result.serverInfo

        # Combine into MCP protocol structure with environment metadata
        result = {
            "environment": {
                "fastmcp": fastmcp.__version__,  # Version generating this manifest
                "mcp": importlib.metadata.version("mcp"),  # MCP protocol version
            },
            "serverInfo": server_info,
            "capabilities": {},  # MCP format doesn't include capabilities at top level
            "tools": tools_result.tools,
            "prompts": prompts_result.prompts,
            "resources": resources_result.resources,
            "resourceTemplates": templates_result.resourceTemplates,
        }

        return pydantic_core.to_json(result, indent=2)


async def format_info(
    mcp: FastMCP[Any] | FastMCP1x,
    format: InspectFormat | Literal["fastmcp", "mcp"],
    info: FastMCPInfo | None = None,
) -> bytes:
    """Format server information according to the specified format.

    Args:
        mcp: The FastMCP instance
        format: Output format ("fastmcp" or "mcp")
        info: Pre-extracted FastMCPInfo (optional, will be extracted if not provided)

    Returns:
        JSON bytes in the requested format
    """
    # Convert string to enum if needed
    if isinstance(format, str):
        format = InspectFormat(format)

    if format == InspectFormat.MCP:
        # MCP format doesn't need FastMCPInfo, it uses Client directly
        return await format_mcp_info(mcp)
    elif format == InspectFormat.FASTMCP:
        # For FastMCP format, we need the FastMCPInfo
        # This works for both v1 and v2 servers
        if info is None:
            info = await inspect_fastmcp(mcp)
        return format_fastmcp_info(info)
    else:
        raise ValueError(f"Unknown format: {format}")
