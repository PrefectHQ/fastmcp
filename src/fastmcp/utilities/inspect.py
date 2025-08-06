"""Utilities for inspecting FastMCP instances."""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import Any

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
    annotations: dict[str, Any] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


@dataclass
class PromptInfo:
    """Information about a prompt."""

    key: str
    name: str
    description: str | None
    arguments: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


@dataclass
class ResourceInfo:
    """Information about a resource."""

    key: str
    uri: str
    name: str | None
    description: str | None
    mime_type: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


@dataclass
class TemplateInfo:
    """Information about a resource template."""

    key: str
    uri_template: str
    name: str | None
    description: str | None
    mime_type: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


@dataclass
class FastMCPInfo:
    """Information extracted from a FastMCP instance."""

    name: str
    instructions: str | None
    fastmcp_version: str
    mcp_version: str
    server_version: str | None
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
    # Get all the components using FastMCP2's direct methods
    tools_dict = await mcp.get_tools()
    prompts_dict = await mcp.get_prompts()
    resources_dict = await mcp.get_resources()
    templates_dict = await mcp.get_resource_templates()

    # Extract detailed tool information
    tool_infos = []
    for key, tool in tools_dict.items():
        # Convert to MCP tool to get input schema
        mcp_tool = tool.to_mcp_tool(name=key)
        tool_infos.append(
            ToolInfo(
                key=key,
                name=tool.name or key,
                description=tool.description,
                input_schema=mcp_tool.inputSchema if mcp_tool.inputSchema else {},
                annotations=tool.annotations.model_dump() if tool.annotations else None,
                tags=list(tool.tags) if tool.tags else None,
                enabled=tool.enabled,
            )
        )

    # Extract detailed prompt information
    prompt_infos = []
    for key, prompt in prompts_dict.items():
        prompt_infos.append(
            PromptInfo(
                key=key,
                name=prompt.name or key,
                description=prompt.description,
                arguments=[arg.model_dump() for arg in prompt.arguments]
                if prompt.arguments
                else None,
                tags=list(prompt.tags) if prompt.tags else None,
                enabled=prompt.enabled,
            )
        )

    # Extract detailed resource information
    resource_infos = []
    for key, resource in resources_dict.items():
        resource_infos.append(
            ResourceInfo(
                key=key,
                uri=key,  # For v2, key is the URI
                name=resource.name,
                description=resource.description,
                mime_type=resource.mime_type,
                tags=list(resource.tags) if resource.tags else None,
                enabled=resource.enabled,
            )
        )

    # Extract detailed template information
    template_infos = []
    for key, template in templates_dict.items():
        template_infos.append(
            TemplateInfo(
                key=key,
                uri_template=key,  # For v2, key is the URI template
                name=template.name,
                description=template.description,
                mime_type=template.mime_type,
                tags=list(template.tags) if template.tags else None,
                enabled=template.enabled,
            )
        )

    # Basic MCP capabilities that FastMCP supports
    capabilities = {
        "tools": {"listChanged": True},
        "resources": {"subscribe": False, "listChanged": False},
        "prompts": {"listChanged": False},
        "logging": {},
    }

    return FastMCPInfo(
        name=mcp.name,
        instructions=mcp.instructions,
        fastmcp_version=fastmcp.__version__,
        mcp_version=importlib.metadata.version("mcp"),
        server_version=(
            mcp.version if hasattr(mcp, "version") else mcp._mcp_server.version
        ),
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
            # Extract annotations if they exist
            annotations = None
            if hasattr(mcp_tool, "annotations") and mcp_tool.annotations:
                if hasattr(mcp_tool.annotations, "model_dump"):
                    annotations = mcp_tool.annotations.model_dump()
                elif isinstance(mcp_tool.annotations, dict):
                    annotations = mcp_tool.annotations
                else:
                    annotations = None

            tool_infos.append(
                ToolInfo(
                    key=mcp_tool.name,  # For 1.x, key and name are the same
                    name=mcp_tool.name,
                    description=mcp_tool.description,
                    input_schema=mcp_tool.inputSchema if mcp_tool.inputSchema else {},
                    annotations=annotations,
                    tags=None,  # 1.x doesn't have tags
                    enabled=None,  # 1.x doesn't have enabled field
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
                    key=mcp_prompt.name,  # For 1.x, key and name are the same
                    name=mcp_prompt.name,
                    description=mcp_prompt.description,
                    arguments=arguments,
                    tags=None,  # 1.x doesn't have tags
                    enabled=None,  # 1.x doesn't have enabled field
                )
            )

        # Extract detailed resource information from MCP Resource objects
        resource_infos = []
        for mcp_resource in mcp_resources:
            resource_infos.append(
                ResourceInfo(
                    key=str(mcp_resource.uri),  # For 1.x, key and uri are the same
                    uri=str(mcp_resource.uri),
                    name=mcp_resource.name,
                    description=mcp_resource.description,
                    mime_type=mcp_resource.mimeType,
                    tags=None,  # 1.x doesn't have tags
                    enabled=None,  # 1.x doesn't have enabled field
                )
            )

        # Extract detailed template information from MCP ResourceTemplate objects
        template_infos = []
        for mcp_template in mcp_templates:
            template_infos.append(
                TemplateInfo(
                    key=str(
                        mcp_template.uriTemplate
                    ),  # For 1.x, key and uriTemplate are the same
                    uri_template=str(mcp_template.uriTemplate),
                    name=mcp_template.name,
                    description=mcp_template.description,
                    mime_type=mcp_template.mimeType,
                    tags=None,  # 1.x doesn't have tags
                    enabled=None,  # 1.x doesn't have enabled field
                )
            )

        # Basic MCP capabilities
        capabilities = {
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
            "logging": {},
        }

        return FastMCPInfo(
            name=mcp._mcp_server.name,
            instructions=mcp._mcp_server.instructions,
            fastmcp_version=importlib.metadata.version("mcp"),
            mcp_version=importlib.metadata.version("mcp"),
            server_version=mcp._mcp_server.version,
            tools=tool_infos,
            prompts=prompt_infos,
            resources=resource_infos,
            templates=template_infos,  # FastMCP1x does have templates
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
        return await inspect_fastmcp_v2(mcp)
