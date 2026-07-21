"""Upgrade-reality tests: does a FastMCP 3.x server survive the move to v4?

These tests are the executable half of the `docs/getting-started/upgrading/from-fastmcp-3`
guide. They fall into three groups:

- `TestCommonServersUpgradeCleanly` builds servers the way the 3.x docs taught
  and runs them end-to-end under v4 defaults. These are the "nothing to do"
  cases — a typical server upgrades untouched.
- `TestRemovedSurfacesFailLoudly` pins every hard removal to the exact error a
  user hits, so the break is a clear signal rather than silent misbehavior.
  Each case names its 4.0 replacement in a comment.
- `TestBehaviorChanges` covers the shifts that compile fine but behave
  differently: the `mode="auto"` client default, path-traversal screening, and
  the resource-not-found error code.

The camelCase field bridge and the `McpError` alias are covered in
`test_compat.py`; this file deliberately does not repeat them.
"""

import importlib
import inspect

import pytest

# Protocol types now live in mcp_types directly; fastmcp.types no longer
# re-exports them (it holds only FastMCP-defined types like Textarea).
from mcp_types import ErrorData, TextContent, Tool, ToolAnnotations

from fastmcp import Client, FastMCP, settings

# The canonical replacement symbols the upgrade guide points users to. Importing
# them here — the ordinary in-process path every other test in the suite uses —
# means this file fails at collection if the guide ever names a symbol that no
# longer resolves. `create_proxy`, `settings`, `McpError`, and
# `CacheableToolResult` above are part of the same set.
from fastmcp.apps import AppConfig
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.dependencies import Depends
from fastmcp.exceptions import McpError
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.server import create_proxy
from fastmcp.server.middleware.caching import CacheableToolResult
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools, ToolTransform
from fastmcp.tools.function_tool import FunctionTool


class TestCommonServersUpgradeCleanly:
    """Servers written against the 3.x API run unchanged on v4 defaults."""

    async def test_basic_tool_resource_prompt_server(self):
        mcp = FastMCP("Demo", instructions="A demo server")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        @mcp.resource("data://config")
        def config() -> dict:
            return {"version": "1.0"}

        @mcp.prompt
        def greet(who: str) -> str:
            return f"Hello, {who}"

        async with Client(mcp) as client:  # default mode="auto"
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
            result = await client.call_tool("add", {"a": 2, "b": 3})

        assert {t.name for t in tools} == {"add"}
        assert {str(r.uri) for r in resources} == {"data://config"}
        assert {p.name for p in prompts} == {"greet"}
        assert result.data == 5

    async def test_templated_resource_server(self):
        mcp = FastMCP("Templated")

        @mcp.resource("files://{name}")
        def get_file(name: str) -> str:
            return f"contents of {name}"

        async with Client(mcp) as client:
            contents = await client.read_resource("files://report.txt")

        assert contents[0].text == "contents of report.txt"

    async def test_mounted_server(self):
        parent = FastMCP("Parent")
        child = FastMCP("Child")

        @child.tool
        def ping() -> str:
            return "pong"

        parent.mount(child, namespace="child")

        async with Client(parent) as client:
            tools = await client.list_tools()
            result = await client.call_tool("child_ping", {})

        assert "child_ping" in {t.name for t in tools}
        assert result.data == "pong"

    async def test_proxy_server(self):
        backend = FastMCP("Backend")

        @backend.tool
        def ping() -> str:
            return "pong"

        proxy = create_proxy(backend)

        async with Client(proxy) as client:
            tools = await client.list_tools()
            result = await client.call_tool("ping", {})

        assert "ping" in {t.name for t in tools}
        assert result.data == "pong"


# --- Canonical replacement surfaces the guide points users to ---


class TestCanonicalReplacementsResolve:
    def test_replacement_symbols_are_bound(self):
        # The imports at the top of this module already prove these resolve
        # (a broken pointer would fail collection). This asserts each is bound
        # so the guarantee is an explicit, named test rather than a side effect.
        symbols = (
            FunctionTool,
            FunctionResource,
            FunctionPrompt,
            OpenAPIProvider,
            FastMCPProxy,
            ProxyClient,
            create_proxy,
            AppConfig,
            ToolTransform,
            PromptsAsTools,
            ResourcesAsTools,
            Depends,
            McpError,
            CacheableToolResult,
            TextContent,
            Tool,
            ToolAnnotations,
            ErrorData,
        )
        assert all(sym is not None for sym in symbols)


# --- Hard removals: modules that no longer exist ---

REMOVED_MODULES = [
    "fastmcp.server.proxy",  # -> fastmcp.server.providers.proxy
    "fastmcp.server.openapi",  # -> fastmcp.server.providers.openapi
    "fastmcp.experimental.server.openapi",  # -> fastmcp.server.providers.openapi
    "fastmcp.experimental.utilities.openapi",  # -> fastmcp.utilities.openapi
    "fastmcp.server.apps",  # -> fastmcp.apps
    "fastmcp.server.app",  # -> fastmcp.apps / fastmcp
    "mcp.types",  # -> mcp_types
]

# Names that were re-export shims and are gone; import them from the canonical
# module (named in each comment) instead.
REMOVED_NAMES = [
    # deprecated 3.1 -> fastmcp.server.transforms.PromptsAsTools / ResourcesAsTools
    ("fastmcp.server.middleware.tool_injection", "PromptToolMiddleware"),
    ("fastmcp.server.middleware.tool_injection", "ResourceToolMiddleware"),
    # old misspelled names renamed to Cacheable* (no alias)  codespell:ignore
    ("fastmcp.server.middleware.caching", "CachableToolResult"),  # codespell:ignore
    ("fastmcp.server.middleware.caching", "CachablePromptResult"),  # codespell:ignore
    # component-import shims -> fastmcp.tools.function_tool, etc.
    ("fastmcp.tools.tool", "FunctionTool"),
    ("fastmcp.resources.resource", "FunctionResource"),
    ("fastmcp.prompts.prompt", "FunctionPrompt"),
]


class TestRemovedSurfacesFailLoudly:
    @pytest.mark.parametrize("module_path", REMOVED_MODULES)
    def test_removed_module_raises_module_not_found(self, module_path):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_path)

    @pytest.mark.parametrize(
        "module_path, name",
        REMOVED_NAMES,
        ids=[f"{m}:{n}" for m, n in REMOVED_NAMES],
    )
    def test_removed_name_is_gone(self, module_path, name):
        # `from <module_path> import <name>` raises ImportError as a result.
        module = importlib.import_module(module_path)
        assert not hasattr(module, name)

    def test_cacheable_rename_new_name_resolves(self):
        assert CacheableToolResult is not None

    @pytest.mark.parametrize(
        "method_name",
        [
            "as_proxy",  # -> create_proxy()
            "import_server",  # -> mount()
            "add_tool_transformation",  # -> add_transform(ToolTransform(...))
            "remove_tool_transformation",  # removed no-op
            "remove_tool",  # -> mcp.local_provider.remove_tool()
        ],
    )
    def test_removed_fastmcp_method_is_gone(self, method_name):
        assert not hasattr(FastMCP, method_name)

    def test_mount_prefix_kwarg_removed(self):
        parent = FastMCP("Parent")
        child = FastMCP("Child")
        # prefix= -> namespace=
        with pytest.raises(TypeError):
            parent.mount(child, prefix="child")  # ty: ignore[unknown-argument]

    def test_mount_as_proxy_kwarg_removed(self):
        parent = FastMCP("Parent")
        child = FastMCP("Child")
        # as_proxy= removed; wrap with create_proxy() before mounting
        with pytest.raises(TypeError):
            parent.mount(child, as_proxy=True)  # ty: ignore[unknown-argument]

    def test_tool_serializer_kwarg_removed(self):
        mcp = FastMCP("S")
        # serializer= -> return a ToolResult
        with pytest.raises(TypeError):

            @mcp.tool(serializer=str)  # ty: ignore[no-matching-overload]
            def f(x: int) -> int:
                return x

    def test_tool_exclude_args_kwarg_removed(self):
        mcp = FastMCP("S")
        # exclude_args= -> Depends() to hide parameters
        with pytest.raises(TypeError):

            @mcp.tool(exclude_args=["y"])  # ty: ignore[no-matching-overload]
            def g(x: int, y: int = 1) -> int:
                return x

    def test_decorator_mode_setting_removed(self):
        # FASTMCP_DECORATOR_MODE / settings.decorator_mode removed entirely
        assert not hasattr(settings, "decorator_mode")

    def test_streamable_http_sse_read_timeout_removed(self):
        # sse_read_timeout= was a no-op under SDK v2; configure via
        # read_timeout_seconds or the httpx2 client factory instead.
        with pytest.raises(TypeError):
            StreamableHttpTransport(
                "https://example.com/mcp",
                sse_read_timeout=5,  # ty: ignore[unknown-argument]
            )

    def test_mcp_error_positional_construction_raises(self):
        # Before: raise McpError(ErrorData(code=..., message=...))
        with pytest.raises(TypeError):
            McpError(ErrorData(code=-32000, message="boom"))  # ty: ignore[missing-argument, invalid-argument-type]

    def test_mcp_error_keyword_construction_works(self):
        err = McpError(code=-32000, message="boom")
        assert err.error.code == -32000
        assert err.error.message == "boom"


class TestBehaviorChanges:
    """Changes that import fine but behave differently on v4."""

    def test_client_defaults_to_auto_mode(self):
        default = inspect.signature(Client.__init__).parameters["mode"].default
        assert default == "auto"

    async def test_templated_resource_blocks_path_traversal(self):
        mcp = FastMCP("Guarded")

        @mcp.resource("files://{path}")
        def guarded(path: str) -> str:
            return f"read:{path}"

        # Same template with screening disabled — the control that proves the
        # rejection below is the path screen, not an unrelated URI mismatch.
        @mcp.resource("open://{path}", security=None)
        def unguarded(path: str) -> str:
            return f"read:{path}"

        async with Client(mcp) as client:
            ok = await client.read_resource("files://hello.txt")
            assert ok[0].text == "read:hello.txt"

            # With screening off, a `..` value reaches the handler...
            control = await client.read_resource("open://..")
            assert control[0].text == "read:.."

            # ...but under the default policy it is screened before the handler
            # runs and surfaces a non-leaky INVALID_PARAMS error.
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("files://..")
            assert exc_info.value.error.code == -32602
            assert "not found" in exc_info.value.error.message.lower()

    async def test_resource_not_found_uses_invalid_params_code(self):
        mcp = FastMCP("NF")

        # Pin the handshake era so we read the code off the wire error directly.
        async with Client(mcp, mode="legacy") as client:
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("missing://nope")

        # SEP-2164: resource-not-found is INVALID_PARAMS (-32602), was -32002.
        assert exc_info.value.error.code == -32602
