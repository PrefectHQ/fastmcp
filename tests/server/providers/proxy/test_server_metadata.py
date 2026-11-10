"""Server metadata forwarding across proxy protocol eras."""

from itertools import product
from typing import Any, Literal, TypeVar

import mcp_types
import pytest
from mcp import MCPError
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp import Client, FastMCP, FastMCPDeprecationWarning
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    ProxyClient,
    ProxyInitializeMiddleware,
    ProxyMetadataMiddleware,
    ProxyProvider,
    StatefulProxyClient,
)
from fastmcp.utilities.http import find_available_port

ResultT = TypeVar("ResultT", bound=mcp_types.Result)

UPSTREAM_INFO = mcp_types.Implementation(
    name="upstream",
    title="Upstream title",
    version="1.2.3",
    description="Upstream description",
    website_url="https://upstream.example.com",
    icons=[mcp_types.Icon(src="https://upstream.example.com/icon.png")],
)


class UpstreamMetadataMiddleware(Middleware):
    """Advertise metadata that differs from the gateway's own claims."""

    def __init__(self, server_info: mcp_types.Implementation = UPSTREAM_INFO) -> None:
        self.server_info = server_info

    def _updates(self, result: mcp_types.Result) -> dict[str, Any]:
        meta = {
            **(result.meta or {}),
            mcp_types.PROTOCOL_VERSION_META_KEY: "upstream-version",
            mcp_types.CLIENT_INFO_META_KEY: {"name": "upstream-client"},
            mcp_types.CLIENT_CAPABILITIES_META_KEY: {"upstream": True},
            "com.example/upstream": {"enabled": True},
            "com.example/shared": "upstream",
        }
        updates: dict[str, Any] = {
            "instructions": "upstream instructions",
            "meta": meta,
        }
        if isinstance(result, mcp_types.InitializeResult):
            updates.update(
                server_info=self.server_info,
                capabilities=mcp_types.ServerCapabilities(
                    experimental={"upstream": {"claimed": True}}
                ),
            )
        else:
            meta[mcp_types.SERVER_INFO_META_KEY] = self.server_info.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
            updates.update(
                ttl_ms=91_000,
                cache_scope="public",
                capabilities=mcp_types.ServerCapabilities(
                    experimental={"upstream": {"claimed": True}}
                ),
            )
        return updates

    async def on_initialize(
        self,
        context: MiddlewareContext[mcp_types.InitializeRequest],
        call_next: CallNext[
            mcp_types.InitializeRequest, mcp_types.InitializeResult | None
        ],
    ) -> mcp_types.InitializeResult | None:
        result = await call_next(context)
        assert result is not None
        return result.model_copy(update=self._updates(result))

    async def on_discover(
        self,
        context: MiddlewareContext[mcp_types.DiscoverRequest],
        call_next: CallNext[
            mcp_types.DiscoverRequest,
            mcp_types.DiscoverResult | dict[str, Any],
        ],
    ) -> mcp_types.DiscoverResult | dict[str, Any]:
        result = await call_next(context)
        if not isinstance(result, mcp_types.DiscoverResult):
            return result
        return result.model_copy(update=self._updates(result))


class FrontendMetadataMiddleware(Middleware):
    """Set frontend values that must win over the upstream on collision."""

    def _update(self, result: ResultT) -> ResultT:
        return result.model_copy(
            update={
                "meta": {
                    **(result.meta or {}),
                    "com.example/shared": "frontend",
                    "com.example/frontend": {"enabled": True},
                },
            }
        )

    async def on_initialize(
        self,
        context: MiddlewareContext[mcp_types.InitializeRequest],
        call_next: CallNext[
            mcp_types.InitializeRequest, mcp_types.InitializeResult | None
        ],
    ) -> mcp_types.InitializeResult | None:
        result = await call_next(context)
        assert result is not None
        return self._update(result)

    async def on_discover(
        self,
        context: MiddlewareContext[mcp_types.DiscoverRequest],
        call_next: CallNext[
            mcp_types.DiscoverRequest,
            mcp_types.DiscoverResult | dict[str, Any],
        ],
    ) -> mcp_types.DiscoverResult | dict[str, Any]:
        result = await call_next(context)
        if not isinstance(result, mcp_types.DiscoverResult):
            return result
        return self._update(result)


def make_upstream() -> FastMCP:
    return FastMCP("unmodified-upstream", middleware=[UpstreamMetadataMiddleware()])


def make_gateway(
    upstream: FastMCP,
    *,
    backend_mode: str,
    identity: Literal["proxy", "upstream"] = "proxy",
    instructions: str | None = None,
    frontend_metadata: bool = False,
) -> FastMCP:
    provider = ProxyProvider(lambda: ProxyClient(upstream, mode=backend_mode))
    metadata = ProxyMetadataMiddleware(provider, identity=identity)
    middleware: list[Middleware] = [metadata]
    if frontend_metadata:
        middleware.append(FrontendMetadataMiddleware())
    gateway = FastMCP(
        "gateway",
        version="9.8.7",
        instructions=instructions,
        providers=[provider],
        middleware=middleware,
        cache_ttl=7,
        cache_scope="private",
    )
    return gateway


@pytest.mark.parametrize(
    ("frontend_mode", "backend_mode"),
    list(product(("legacy", "auto"), repeat=2)),
)
async def test_forwards_metadata_across_all_protocol_era_combinations(
    frontend_mode: str, backend_mode: str
):
    gateway = make_gateway(make_upstream(), backend_mode=backend_mode)

    async with Client(gateway, mode=frontend_mode) as client:
        result = client.session.initialize_result or client.session.discover_result
        assert result is not None
        assert client.instructions == "upstream instructions"
        assert client.server_info is not None
        assert client.server_info.name == "gateway"
        assert result.meta is not None
        assert result.meta["com.example/upstream"] == {"enabled": True}
        for key in (
            mcp_types.PROTOCOL_VERSION_META_KEY,
            mcp_types.CLIENT_INFO_META_KEY,
            mcp_types.CLIENT_CAPABILITIES_META_KEY,
        ):
            assert key not in result.meta
        stamped_info = result.meta.get(mcp_types.SERVER_INFO_META_KEY)
        assert result.capabilities.experimental is None

        if isinstance(result, mcp_types.InitializeResult):
            assert result.protocol_version not in MODERN_PROTOCOL_VERSIONS
            assert stamped_info is None
        else:
            assert stamped_info is not None
            assert stamped_info["name"] == "gateway"
            assert result.supported_versions == list(MODERN_PROTOCOL_VERSIONS)
            assert result.ttl_ms == 7_000
            assert result.cache_scope == "private"
            assert result.result_type == "complete"


@pytest.mark.parametrize("frontend_mode", ["legacy", "auto"])
@pytest.mark.parametrize("identity", ["proxy", "upstream"])
async def test_identity_policy_forwards_full_implementation(
    frontend_mode: str, identity: Literal["proxy", "upstream"]
):
    gateway = make_gateway(make_upstream(), backend_mode="auto", identity=identity)

    async with Client(gateway, mode=frontend_mode) as client:
        assert client.server_info is not None
        if identity == "proxy":
            assert client.server_info.name == "gateway"
            assert client.server_info.version == "9.8.7"
        else:
            assert client.server_info == UPSTREAM_INFO
            result = client.session.initialize_result or client.session.discover_result
            assert result is not None
            if isinstance(result, mcp_types.InitializeResult):
                assert mcp_types.SERVER_INFO_META_KEY not in (result.meta or {})
            else:
                assert result.meta is not None
                assert result.meta[mcp_types.SERVER_INFO_META_KEY]["name"] == "upstream"


@pytest.mark.parametrize("frontend_mode", ["legacy", "auto"])
async def test_frontend_values_take_precedence(frontend_mode: str):
    gateway = make_gateway(
        make_upstream(),
        backend_mode="auto",
        instructions="frontend instructions",
        frontend_metadata=True,
    )

    async with Client(gateway, mode=frontend_mode) as client:
        result = client.session.initialize_result or client.session.discover_result
        assert result is not None
        assert client.instructions == "frontend instructions"
        assert result.meta is not None
        assert result.meta["com.example/shared"] == "frontend"
        assert result.meta["com.example/frontend"] == {"enabled": True}
        assert result.meta["com.example/upstream"] == {"enabled": True}


async def test_forwards_backend_logs_while_reading_metadata():
    messages: list[str] = []

    class LogOnInitialize(Middleware):
        async def on_initialize(
            self,
            context: MiddlewareContext[mcp_types.InitializeRequest],
            call_next: CallNext[
                mcp_types.InitializeRequest, mcp_types.InitializeResult | None
            ],
        ) -> mcp_types.InitializeResult | None:
            result = await call_next(context)
            assert context.fastmcp_context is not None
            await context.fastmcp_context.log("metadata connection")
            return result

    async def capture_log(message: LogMessage) -> None:
        messages.append(message.data["msg"])

    upstream = FastMCP("upstream", middleware=[LogOnInitialize()])
    proxy = create_proxy(upstream)

    async with Client(proxy, mode="legacy", log_handler=capture_log):
        pass

    assert messages == ["metadata connection"]


async def test_pinned_client_uses_prior_discover_metadata():
    prior_info = mcp_types.Implementation(name="prior", version="1.0")
    prior = mcp_types.DiscoverResult(
        supported_versions=[MODERN_PROTOCOL_VERSIONS[0]],
        capabilities=mcp_types.ServerCapabilities(),
        instructions="prior instructions",
        meta={
            mcp_types.SERVER_INFO_META_KEY: prior_info.model_dump(
                by_alias=True, mode="json"
            ),
            "com.example/prior": True,
        },
    )
    provider = ProxyProvider(
        lambda: ProxyClient(
            make_upstream(),
            mode=MODERN_PROTOCOL_VERSIONS[0],
            prior_discover=prior,
        )
    )
    gateway = FastMCP(
        "gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider, identity="upstream")],
    )

    async with Client(gateway, mode="auto") as client:
        result = client.session.discover_result
        assert result is not None
        assert client.instructions == "prior instructions"
        assert client.server_info == prior_info
        assert result.meta is not None
        assert result.meta["com.example/prior"] is True


async def test_connected_pinned_client_probes_without_adopting_metadata():
    version = MODERN_PROTOCOL_VERSIONS[0]
    upstream = make_upstream()
    async with Client(upstream, mode=version) as backend_client:
        assert backend_client.instructions is None
        proxy = create_proxy(backend_client, identity="upstream")

        async with Client(proxy, mode="auto") as client:
            result = client.session.discover_result
            assert result is not None
            assert client.instructions == "upstream instructions"
            assert client.server_info == UPSTREAM_INFO
            assert result.meta is not None
            assert result.meta["com.example/upstream"] == {"enabled": True}

        assert backend_client.instructions is None


async def test_invalid_upstream_discovery_metadata_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
):
    version = MODERN_PROTOCOL_VERSIONS[0]

    async def invalid_discover(_version: str) -> dict[str, Any]:
        return {
            "resultType": "complete",
            "supportedVersions": [version],
            "capabilities": [],
        }

    async with ProxyClient(make_upstream(), mode=version) as backend_client:
        monkeypatch.setattr(backend_client.session, "send_discover", invalid_discover)
        proxy = create_proxy(backend_client)

        async with Client(proxy, mode="auto") as client:
            assert client.server_info is not None
            assert client.server_info.name == proxy.name
            assert await client.list_tools() == []


async def test_invalid_backend_client_negotiation_is_not_ignored():
    version = MODERN_PROTOCOL_VERSIONS[0]
    prior = mcp_types.DiscoverResult(
        supported_versions=["2099-01-01"],
        capabilities=mcp_types.ServerCapabilities(),
    )
    provider = ProxyProvider(
        lambda: ProxyClient(
            make_upstream(),
            mode=version,
            prior_discover=prior,
        )
    )
    gateway = FastMCP(
        "gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider)],
    )

    with pytest.raises(MCPError):
        async with Client(gateway, mode="auto"):
            pass


async def test_unrelated_client_validation_error_is_not_ignored():
    class InvalidClient(ProxyClient):
        async def __aenter__(self) -> ProxyClient:
            mcp_types.Implementation.model_validate({})
            return self

    provider = ProxyProvider(lambda: InvalidClient(make_upstream()))
    gateway = FastMCP(
        "gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider)],
    )

    with pytest.raises(MCPError):
        async with Client(gateway, mode="auto"):
            pass


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_forwarded_metadata_does_not_alias_connected_backend(mode: str):
    backend_info = mcp_types.Implementation(name="shared-backend", version="1.0")
    upstream = FastMCP(
        "upstream",
        middleware=[UpstreamMetadataMiddleware(backend_info)],
    )

    class MutateForwardedMetadata(Middleware):
        def _mutate(self, result: ResultT) -> ResultT:
            assert result.meta is not None
            nested = result.meta["com.example/upstream"]
            assert isinstance(nested, dict)
            nested["enabled"] = False
            if isinstance(result, mcp_types.InitializeResult):
                result.server_info.name = "frontend mutation"
            else:
                server_info = result.meta[mcp_types.SERVER_INFO_META_KEY]
                assert isinstance(server_info, dict)
                server_info["name"] = "frontend mutation"
            return result

        async def on_initialize(self, context, call_next):
            result = await call_next(context)
            assert result is not None
            return self._mutate(result)

        async def on_discover(self, context, call_next):
            result = await call_next(context)
            if not isinstance(result, mcp_types.DiscoverResult):
                return result
            return self._mutate(result)

    async with Client(upstream, mode=mode) as backend_client:
        provider = ProxyProvider(lambda: backend_client)
        gateway = FastMCP(
            "gateway",
            providers=[provider],
            middleware=[
                MutateForwardedMetadata(),
                ProxyMetadataMiddleware(provider, identity="upstream"),
            ],
        )

        async with Client(gateway, mode=mode):
            pass

        backend_result = (
            backend_client.session.initialize_result
            or backend_client.session.discover_result
        )
        assert backend_result is not None
        assert backend_result.meta is not None
        assert backend_result.meta["com.example/upstream"] == {"enabled": True}
        assert backend_client.server_info == backend_info


async def test_disconnected_pinned_client_is_not_cloned():
    class UnclonableProxyClient(ProxyClient):
        def new(self) -> ProxyClient:
            raise AssertionError("metadata client must not be cloned")

    version = MODERN_PROTOCOL_VERSIONS[0]
    provider = ProxyProvider(
        lambda: UnclonableProxyClient(make_upstream(), mode=version)
    )
    gateway = FastMCP(
        "gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider, identity="upstream")],
    )

    async with Client(gateway, mode="auto") as client:
        assert client.instructions == "upstream instructions"
        assert client.server_info == UPSTREAM_INFO


async def test_stateful_pinned_metadata_uses_registered_client_lifecycle():
    created: list[StatefulProxyClient] = []

    class TrackingStatefulProxyClient(StatefulProxyClient):
        def new(self) -> StatefulProxyClient:
            client = super().new()
            created.append(client)
            return client

    version = MODERN_PROTOCOL_VERSIONS[0]
    stateful_client = TrackingStatefulProxyClient(make_upstream(), mode=version)
    proxy = FastMCPProxy(
        name="stateful-proxy",
        client_factory=stateful_client.new_stateful,
        identity="upstream",
    )

    async with Client(proxy, mode="auto") as client:
        assert client.instructions == "upstream instructions"
        assert client.server_info == UPSTREAM_INFO

    assert len(created) == 1
    assert not created[0].is_connected()


@pytest.mark.parametrize("frontend_mode", ["legacy", "auto"])
@pytest.mark.parametrize("async_factory", [False, True])
@pytest.mark.parametrize("error_kind", ["runtime", "mcp"])
async def test_client_factory_errors_are_not_swallowed(
    frontend_mode: str,
    async_factory: bool,
    error_kind: Literal["runtime", "mcp"],
):
    def factory_error() -> Exception:
        if error_kind == "mcp":
            return MCPError(
                code=mcp_types.INTERNAL_ERROR,
                message="broken client factory",
            )
        return RuntimeError("broken client factory")

    def broken_factory() -> Client:
        raise factory_error()

    async def broken_async_factory() -> Client:
        raise factory_error()

    factory = broken_async_factory if async_factory else broken_factory
    provider = ProxyProvider(factory)
    gateway = FastMCP(
        "gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider)],
    )

    with pytest.raises(MCPError):
        async with Client(gateway, mode=frontend_mode):
            pass


@pytest.mark.parametrize("frontend_mode", ["legacy", "auto"])
async def test_unavailable_backend_does_not_block_connection(frontend_mode: str):
    port = find_available_port()
    provider = ProxyProvider(
        lambda: ProxyClient(
            StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp"), mode="auto"
        ),
        cache_ttl=0,
    )
    gateway = FastMCP(
        "available-gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider)],
    )
    gateway.provider_error_strategy = "raise"

    async with Client(gateway, mode=frontend_mode) as client:
        assert client.server_info is not None
        assert client.server_info.name == "available-gateway"
        with pytest.raises(MCPError, match="Client failed to connect"):
            await client.list_tools()


async def test_extension_owned_discovery_result_bypasses_metadata_forwarding():
    factory_called = False

    def broken_factory() -> Client:
        nonlocal factory_called
        factory_called = True
        raise RuntimeError("metadata should not be read")

    async def custom_discover(_ctx, _params):
        return {
            "resultType": "com.example/custom",
            "payload": {"enabled": True},
        }

    provider = ProxyProvider(broken_factory)
    gateway = FastMCP(
        "extension-gateway",
        middleware=[ProxyMetadataMiddleware(provider)],
    )
    gateway._mcp_server.add_request_handler(
        "server/discover", mcp_types.RequestParams, custom_discover
    )

    version = MODERN_PROTOCOL_VERSIONS[0]
    async with Client(gateway, mode=version) as client:
        result = await client.session.send_discover(version)

    assert isinstance(result, dict)
    assert result["payload"] == {"enabled": True}
    assert not factory_called


def test_gateway_construction_does_not_create_backend_client():
    calls = 0

    def client_factory() -> ProxyClient:
        nonlocal calls
        calls += 1
        return ProxyClient(make_upstream())

    provider = ProxyProvider(client_factory)
    FastMCP(
        "lazy-gateway",
        providers=[provider],
        middleware=[ProxyMetadataMiddleware(provider)],
    )

    assert calls == 0


async def test_proxy_initialize_middleware_preserves_legacy_behavior():
    upstream = FastMCP("upstream", instructions="legacy instructions")

    def client_factory() -> ProxyClient:
        return ProxyClient(upstream)

    proxy = FastMCPProxy(name="compatibility-proxy", client_factory=client_factory)

    with pytest.warns(
        FastMCPDeprecationWarning,
        match="`ProxyInitializeMiddleware` is deprecated",
    ):
        middleware = ProxyInitializeMiddleware(proxy)

    proxy.middleware = [middleware]
    async with Client(proxy, mode="legacy") as client:
        assert client.instructions == "legacy instructions"
    async with Client(proxy, mode="auto") as client:
        assert client.instructions is None

    assert middleware.proxy is proxy


async def test_fastmcp_proxy_uses_public_metadata_middleware():
    proxy = create_proxy(make_upstream(), name="convenience", identity="upstream")

    assert any(
        isinstance(middleware, ProxyMetadataMiddleware)
        for middleware in proxy.middleware
    )
    async with Client(proxy, mode="auto") as client:
        assert client.instructions == "upstream instructions"
        assert client.server_info == UPSTREAM_INFO
