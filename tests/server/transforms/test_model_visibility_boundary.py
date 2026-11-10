"""App-only tools must not reach the model through server-driven surfaces.

`tools/list` carries app-only tools on purpose — intermediaries need them to
forward, and the MCP Apps spec puts visibility filtering on the host. That
division holds only where a host sits between the server and the model.

A search result, a code-mode catalog, and a call-tool proxy are all driven by
the server itself: the first two reach the model as ordinary tool output, and
the third invokes on a name the model supplies. No host mediates any of them,
so the visibility declaration has to be applied server-side.
"""

from __future__ import annotations

import json

import pytest

from fastmcp import Client, FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError
from fastmcp.experimental.transforms.code_mode import CodeMode
from fastmcp.server.providers.addressing import hashed_backend_name
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
from fastmcp.tools.base import Tool


def build_server_without_transform() -> FastMCP:
    return _build(None)


def build_server(transform) -> FastMCP:
    return _build(transform)


def _build(transform) -> FastMCP:
    app = FastMCPApp("contacts")

    @app.tool()
    def save_contact(name: str) -> str:
        """UI-only backend that writes a contact."""
        return f"saved {name}"

    @app.tool(model=True)
    def search_contacts(query: str) -> str:
        """Model-visible backend."""
        return f"found {query}"

    @app.ui()
    def contacts_ui() -> str:
        return "ui"

    server = FastMCP("Platform")
    server.add_provider(app)
    if transform is not None:
        server.add_transform(transform)
    return server


CATALOG_TRANSFORMS = [
    pytest.param(RegexSearchTransform, id="regex-search"),
    pytest.param(BM25SearchTransform, id="bm25-search"),
    pytest.param(CodeMode, id="code-mode"),
]


@pytest.mark.parametrize("transform_cls", CATALOG_TRANSFORMS)
async def test_app_only_tools_stay_out_of_model_catalogs(transform_cls):
    """Discovery surfaces hand tool definitions straight to the model."""
    server = build_server(transform_cls())

    async with Client(server) as client:
        blob = ""
        for tool in await client.list_tools():
            if "search" not in tool.name:
                continue
            # Each transform names its search argument differently; the
            # schema is the authority.
            (argument,) = (tool.input_schema or {}).get("required", ["query"])
            result = await client.call_tool(tool.name, {argument: "search_contacts"})
            blob += json.dumps(result.structured_content or "")
            blob += "".join(
                block.text for block in result.content if hasattr(block, "text")
            )

    assert blob, "no search surface produced output"
    assert "save_contact" not in blob
    assert "search_contacts" in blob


async def test_app_only_tools_are_listed_for_forwarding():
    """The wire listing keeps them: a proxy cannot forward what it cannot see.

    Only the model-facing catalog is filtered, so a server without a catalog
    transform still advertises the tool and its declaration for a host to
    act on.
    """
    plain = build_server_without_transform()

    async with Client(plain) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}

    assert "save_contact" in listed
    assert listed["save_contact"].meta is not None
    assert listed["save_contact"].meta["ui"]["visibility"] == ["app"]


async def test_call_tool_proxy_refuses_undiscoverable_tools():
    """The proxy takes a model-supplied name, so it is a second door in."""
    server = build_server(RegexSearchTransform())

    async with Client(server) as client:
        with pytest.raises(ToolError, match="save_contact"):
            await client.call_tool(
                "call_tool",
                {"name": "save_contact", "arguments": {"name": "eve"}},
            )

        allowed = await client.call_tool(
            "call_tool",
            {"name": "search_contacts", "arguments": {"query": "ada"}},
        )
        assert allowed.content[0].text == "found ada"  # type: ignore[union-attr]


@pytest.mark.parametrize("transform_cls", CATALOG_TRANSFORMS)
async def test_the_apps_own_ui_still_reaches_its_backend(transform_cls):
    """The point of the boundary is the audience, not the tool: a UI calling
    by identity is not the model, and must still work.
    """
    server = build_server(transform_cls())

    result = await server.call_tool(
        hashed_backend_name("contacts", "save_contact"), {"name": "ada"}
    )
    assert result.content[0].text == "saved ada"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


async def test_visibility_is_checked_on_the_version_a_name_reaches():
    """A bare name selects the highest version, so that is the one whose
    declaration governs. Checking before deduplication would advertise a
    model-visible older version whose name runs an app-only newer one.
    """

    def versioned(version: str, visibility: list[str], marker: str) -> Tool:
        def same() -> str:
            return f"ran {marker}"

        return Tool.from_function(
            same, name="same", version=version, meta={"ui": {"visibility": visibility}}
        )

    app = FastMCPApp("contacts")
    app.add_tool(versioned("1.0.0", ["app", "model"], "v1"))
    app.add_tool(versioned("2.0.0", ["app"], "v2"))

    server = FastMCP("Platform")
    server.add_provider(app)
    server.add_transform(RegexSearchTransform())

    async with Client(server) as client:
        found = await client.call_tool("search_tools", {"pattern": "same"})
        blob = json.dumps(found.structured_content or "") + "".join(
            block.text for block in found.content if hasattr(block, "text")
        )
        assert "same" not in blob

        with pytest.raises(ToolError, match="same"):
            await client.call_tool("call_tool", {"name": "same", "arguments": {}})
