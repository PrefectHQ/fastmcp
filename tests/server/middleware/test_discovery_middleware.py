"""Tests for typed middleware support during modern discovery."""

from typing import Any

import mcp_types
from mcp_types.version import LATEST_MODERN_VERSION

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


async def test_on_discover_receives_and_transforms_typed_result():
    class DiscoveryMiddleware(Middleware):
        def __init__(self) -> None:
            self.request: mcp_types.DiscoverRequest | None = None
            self.result: mcp_types.DiscoverResult | None = None

        async def on_discover(
            self,
            context: MiddlewareContext[mcp_types.DiscoverRequest],
            call_next: CallNext[
                mcp_types.DiscoverRequest,
                mcp_types.DiscoverResult | dict[str, Any],
            ],
        ) -> mcp_types.DiscoverResult | dict[str, Any]:
            self.request = context.message
            result = await call_next(context)
            assert isinstance(result, mcp_types.DiscoverResult)
            self.result = result
            return result.model_copy(update={"instructions": "discovered"})

    middleware = DiscoveryMiddleware()
    server = FastMCP("typed-discovery", middleware=[middleware])

    async with Client(server, mode="auto") as client:
        assert client.instructions == "discovered"

    assert isinstance(middleware.request, mcp_types.DiscoverRequest)
    assert isinstance(middleware.result, mcp_types.DiscoverResult)


async def test_on_discover_forwards_modified_params():
    modified = False
    server = FastMCP("modified-discovery")
    default_handler = server._mcp_server._handle_discover

    async def capture_params(ctx, params):
        nonlocal modified
        assert params is not None
        assert params.meta is not None
        modified = params.meta["com.example/modified"] is True
        return await default_handler(ctx, params)

    server._mcp_server.add_request_handler(
        "server/discover", mcp_types.RequestParams, capture_params
    )

    class ModifyParams(Middleware):
        async def on_discover(self, context, call_next):
            assert context.message.params is not None
            assert context.message.params.meta is not None
            context.message.params = mcp_types.RequestParams(
                meta={
                    **context.message.params.meta,
                    "com.example/modified": True,
                }
            )
            return await call_next(context)

    server.add_middleware(ModifyParams())

    async with Client(server, mode="auto"):
        pass

    assert modified


async def test_on_discover_preserves_extension_owned_result():
    extension_result = {
        "resultType": "com.example/custom",
        "payload": {"enabled": True},
    }

    async def custom_discover(_ctx, _params):
        return extension_result

    class ObserveExtension(Middleware):
        def __init__(self) -> None:
            self.result: mcp_types.DiscoverResult | dict[str, Any] | None = None

        async def on_discover(self, context, call_next):
            self.result = await call_next(context)
            return self.result

    middleware = ObserveExtension()
    server = FastMCP("extension-discovery", middleware=[middleware])
    server._mcp_server.add_request_handler(
        "server/discover", mcp_types.RequestParams, custom_discover
    )

    async with Client(server, mode=LATEST_MODERN_VERSION) as client:
        result = await client.session.send_discover(LATEST_MODERN_VERSION)

    assert isinstance(result, dict)
    assert result["resultType"] == "com.example/custom"
    assert result["payload"] == {"enabled": True}
    assert isinstance(middleware.result, dict)
    assert middleware.result["payload"] == {"enabled": True}
