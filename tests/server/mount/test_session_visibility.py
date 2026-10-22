"""Mounted session visibility must agree between listing and dispatch."""

from typing import Literal

import pytest
from mcp_types import TextContent, TextResourceContents

from fastmcp import Client, FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.providers.addressing import hashed_backend_name


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("component", ["tool", "resource", "template", "prompt"])
async def test_child_session_enable_allows_dispatch(
    mode: Literal["auto", "legacy"], nested: bool, component: str
) -> None:
    child = FastMCP("child")

    @child.tool
    async def unlock(ctx: Context) -> str:
        await ctx.enable_components(tags={"premium"})
        return "unlocked"

    @child.tool(tags={"premium"})
    def premium_tool() -> str:
        return "tool ok"

    @child.resource("data://premium", tags={"premium"})
    def premium_resource() -> str:
        return "resource ok"

    @child.resource("data://premium/{item}", tags={"premium"})
    def premium_template(item: str) -> str:
        return f"template {item}"

    @child.prompt(tags={"premium"})
    def premium_prompt() -> str:
        return "prompt ok"

    child.disable(tags={"premium"})
    parent = FastMCP("parent")
    parent.mount(child, namespace="child")
    namespace = "child"
    if nested:
        root = FastMCP("root")
        root.mount(parent, namespace="parent")
        parent = root
        namespace = "parent_child"
    uri_namespace = namespace.replace("_", "/")

    async with Client(parent, mode=mode) as client:
        await client.call_tool(f"{namespace}_unlock", {})
        if component == "tool":
            assert f"{namespace}_premium_tool" in {
                tool.name for tool in await client.list_tools()
            }
            tool_result = await client.call_tool(f"{namespace}_premium_tool", {})
            assert tool_result.data == "tool ok"
        elif component == "resource":
            uri = f"data://{uri_namespace}/premium"
            assert uri in {
                str(resource.uri) for resource in await client.list_resources()
            }
            resource_result = await client.read_resource(uri)
            assert isinstance(resource_result[0], TextResourceContents)
            assert resource_result[0].text == "resource ok"
        elif component == "template":
            assert f"data://{uri_namespace}/premium/{{item}}" in {
                template.uri_template
                for template in await client.list_resource_templates()
            }
            template_result = await client.read_resource(
                f"data://{uri_namespace}/premium/one"
            )
            assert isinstance(template_result[0], TextResourceContents)
            assert template_result[0].text == "template one"
        else:
            assert f"{namespace}_premium_prompt" in {
                prompt.name for prompt in await client.list_prompts()
            }
            prompt_result = await client.get_prompt(f"{namespace}_premium_prompt")
            assert isinstance(prompt_result.messages[0].content, TextContent)
            assert prompt_result.messages[0].content.text == "prompt ok"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_child_session_enable_allows_hashed_app_dispatch(
    mode: Literal["auto", "legacy"],
) -> None:
    app = FastMCPApp("premium")

    @app.tool()
    def premium_tool() -> str:
        return "app ok"

    child = FastMCP("child", providers=[app])
    child.disable(names={"premium_tool"})

    @child.tool
    async def unlock(ctx: Context) -> None:
        await ctx.enable_components(names={"premium_tool"})

    parent = FastMCP("parent")
    parent.mount(child, namespace="child")
    async with Client(parent, mode=mode) as client:
        await client.call_tool("child_unlock", {})
        result = await client.call_tool(
            hashed_backend_name("premium", "premium_tool"), {}
        )
        assert result.data == "app ok"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_child_rules_stay_isolated_and_reset(
    mode: Literal["auto", "legacy"],
) -> None:
    child = FastMCP("child")

    @child.tool(tags={"premium"})
    def premium_tool() -> str:
        return "child ok"

    @child.tool
    async def unlock(ctx: Context) -> None:
        await ctx.enable_components(tags={"premium"})

    @child.tool
    async def reset(ctx: Context) -> None:
        await ctx.reset_visibility()

    child.disable(tags={"premium"})
    sibling = FastMCP("sibling")

    @sibling.tool(tags={"premium"})
    def sibling_tool() -> str:
        return "sibling ok"

    sibling.disable(tags={"premium"})
    parent = FastMCP("parent")
    parent.mount(child, namespace="child")
    parent.mount(sibling, namespace="sibling")

    @parent.tool
    async def parent_rule(ctx: Context) -> None:
        await ctx.disable_components(names={"child_premium_tool"})

    async with Client(parent, mode=mode) as first, Client(parent, mode=mode) as second:
        await first.call_tool("child_unlock", {})
        assert (await first.call_tool("child_premium_tool", {})).data == "child ok"
        denied = [(first, "sibling_sibling_tool")]
        # Modern in-memory clients share process identity; legacy clients
        # have distinct sessions even when they run in the same process.
        if mode == "legacy":
            denied.append((second, "child_premium_tool"))
        for client, name in denied:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool(name, {})
        await first.call_tool("child_reset", {})
        with pytest.raises(ToolError, match="Unknown tool"):
            await first.call_tool("child_premium_tool", {})
        await first.call_tool("child_unlock", {})
        await first.call_tool("parent_rule", {})
        assert "child_premium_tool" not in {
            tool.name for tool in await first.list_tools()
        }
        with pytest.raises(ToolError, match="Unknown tool"):
            await first.call_tool("child_premium_tool", {})
