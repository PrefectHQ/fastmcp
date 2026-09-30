"""Provider-bundled extensions across composition, configuration, and startup."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Literal

import mcp_types
import pytest
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import METHOD_NOT_FOUND, RequestParams

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    ToolCallContinuation,
    ToolCallOutcome,
)
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers import LocalProvider, Provider
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
from fastmcp.server.transforms import Namespace
from fastmcp_tasks import TasksExtension

EXT_ID = "com.example/bundled"


class InspectRequest(mcp_types.Request):
    method: Literal["bundled/inspect"] = "bundled/inspect"
    params: RequestParams


class OptionalRequest(mcp_types.Request):
    method: Literal["bundled/optional"] = "bundled/optional"
    params: RequestParams


class InspectResult(mcp_types.Result):
    server: str
    tools: list[str]


class BundledExtension(ServerExtension):
    identifier = EXT_ID
    auto_register = True

    def __init__(self, *, optional_method: bool = True) -> None:
        self.optional_method = optional_method
        self.lifecycle: list[str] = []

    def settings(self) -> dict[str, Any]:
        return {"optionalMethod": self.optional_method}

    def methods(self) -> Sequence[MethodBinding]:
        bindings = [MethodBinding("bundled/inspect", RequestParams, self.inspect)]
        if self.optional_method:
            bindings.append(
                MethodBinding("bundled/optional", RequestParams, self.inspect)
            )
        return bindings

    async def inspect(
        self, ctx: ServerRequestContext[Any, Any], params: RequestParams
    ) -> InspectResult:
        return InspectResult(
            server=self.server.name,
            tools=[tool.name for tool in await self.server.list_tools()],
        )

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        self.lifecycle.append("enter")
        try:
            yield
        finally:
            self.lifecycle.append("exit")


class BundledProvider(Provider):
    def __init__(self, extension: ServerExtension | None = None) -> None:
        super().__init__()
        self.extension = extension if extension is not None else BundledExtension()

    def required_extensions(self) -> Sequence[ServerExtension]:
        return [self.extension]


class AuditExtension(BundledExtension):
    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__()
        self.enabled = enabled
        self.calls: list[str] = []

    def settings(self) -> dict[str, Any]:
        return {"enabled": self.enabled}

    async def intercept_tool_call(
        self,
        params: mcp_types.CallToolRequestParams,
        context: Context,
        call_next: ToolCallContinuation,
    ) -> ToolCallOutcome:
        if self.enabled:
            self.calls.append(params.name)
        return await call_next()


@pytest.mark.parametrize("construction", ["constructor", "add_provider"])
async def test_bundled_extension_serves_capability_and_method(construction: str):
    provider = BundledProvider()
    if construction == "constructor":
        server = FastMCP("root", providers=[provider])
    else:
        server = FastMCP("root")
        server.add_provider(provider)

    async with Client(server, mode="auto") as client:
        assert client.server_capabilities.extensions[EXT_ID] == {"optionalMethod": True}
        result = await client.session.send_request(
            InspectRequest(params=RequestParams()), InspectResult
        )
        assert result.server == "root"


@pytest.mark.parametrize("wrapper", ["aggregate", "namespace", "nested"])
async def test_composed_providers_preserve_bundled_extensions(wrapper: str):
    provider: Provider = BundledProvider()
    if wrapper == "aggregate":
        provider = AggregateProvider([provider])
    elif wrapper == "namespace":
        provider = provider.wrap_transform(Namespace("ns"))
    else:
        provider = AggregateProvider(
            [AggregateProvider([provider.wrap_transform(Namespace("inner"))])]
        ).wrap_transform(Namespace("outer"))
    server = FastMCP("root", providers=[provider])
    async with Client(server, mode="auto") as client:
        assert EXT_ID in client.server_capabilities.extensions


@pytest.mark.parametrize("attachment", ["mount", "add_provider", "adapter"])
async def test_mounted_extensions_use_root_registry_and_preserve_child(attachment: str):
    child = FastMCP("child")
    extension = BundledExtension(optional_method=False)
    child.add_extension(extension)

    @child.tool
    def greet() -> str:
        return "hello"

    middle = FastMCP("middle")
    if attachment == "mount":
        middle.mount(child, namespace="child", tool_names={"greet": "hello"})
    else:
        provider = child if attachment == "add_provider" else FastMCPProvider(child)
        middle.add_provider(provider, namespace="child")
    root = FastMCP("root")
    root.mount(middle, namespace="middle")

    assert extension.server is child
    assert root._extensions[EXT_ID] is not middle._extensions[EXT_ID]
    async with Client(root, mode="auto") as client:
        result = await client.session.send_request(
            InspectRequest(params=RequestParams()), InspectResult
        )
        assert result.server == "root"
        expected = "hello" if attachment == "mount" else "greet"
        assert result.tools == [f"middle_child_{expected}"]
        assert client.server_capabilities.extensions[EXT_ID] == {
            "optionalMethod": False
        }
    async with Client(child, mode="auto") as client:
        result = await client.session.send_request(
            InspectRequest(params=RequestParams()), InspectResult
        )
        assert result.server == "child"
        assert result.tools == ["greet"]


def test_one_provider_can_be_used_by_independent_servers():
    provider = BundledProvider()
    first = FastMCP("first", providers=[provider])
    second = FastMCP("second", providers=[provider])
    assert first._extensions[EXT_ID].server is first
    assert second._extensions[EXT_ID].server is second
    assert first._extensions[EXT_ID] is not second._extensions[EXT_ID]
    with pytest.raises(RuntimeError, match="not bound"):
        _ = provider.extension.server


@pytest.mark.parametrize("explicit_first", [True, False])
async def test_explicit_configuration_wins_and_removes_optional_method(
    explicit_first: bool,
):
    server = FastMCP("root")
    explicit = BundledExtension(optional_method=False)
    if explicit_first:
        server.add_extension(explicit)
    server.add_provider(BundledProvider())
    if not explicit_first:
        server.add_extension(explicit)

    async with Client(server, mode="auto") as client:
        assert client.server_capabilities.extensions[EXT_ID] == {
            "optionalMethod": False
        }
        result = await client.session.send_request(
            InspectRequest(params=RequestParams()), InspectResult
        )
        assert result.server == "root"
        with pytest.raises(MCPError) as exc:
            await client.session.send_request(
                OptionalRequest(params=RequestParams()), InspectResult
            )
        assert exc.value.error.code == METHOD_NOT_FOUND
    assert server._extensions[EXT_ID] is explicit
    assert explicit.lifecycle == ["enter", "exit"]


def test_duplicate_explicit_registration_still_raises():
    server = FastMCP("root", providers=[BundledProvider()])
    server.add_extension(BundledExtension())
    with pytest.raises(ValueError, match="already registered"):
        server.add_extension(BundledExtension())


def test_same_bundled_configuration_is_deduplicated(caplog: pytest.LogCaptureFixture):
    server = FastMCP("root")
    with caplog.at_level(logging.WARNING):
        server.add_provider(AggregateProvider([BundledProvider(), BundledProvider()]))
    assert len(server._extensions) == 1
    assert "Conflicting" not in caplog.text


async def test_conflicting_settings_warn_once_and_keep_first(
    caplog: pytest.LogCaptureFixture,
):
    server = FastMCP("root")
    with caplog.at_level(logging.WARNING):
        server.add_provider(BundledProvider(BundledExtension(optional_method=False)))
        first = server._extensions[EXT_ID]
        server.add_provider(BundledProvider())
        async with Client(server, mode="auto"):
            assert server._extensions[EXT_ID] is first
    messages = [r.message for r in caplog.records if "Conflicting" in r.message]
    assert len(messages) == 1
    assert "add_extension()" in messages[0]


def test_explicit_configuration_suppresses_conflict_warning(
    caplog: pytest.LogCaptureFixture,
):
    server = FastMCP("root")
    server.add_extension(BundledExtension(optional_method=False))
    with caplog.at_level(logging.WARNING):
        server.add_provider(BundledProvider())
    assert "Conflicting" not in caplog.text


def test_registration_debug_log_names_extension_provider_and_server(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.DEBUG, logger="fastmcp.server.mixins.extensions"):
        FastMCP("receiving-server", providers=[BundledProvider()])
    assert EXT_ID in caplog.text
    assert "BundledProvider" in caplog.text
    assert "receiving-server" in caplog.text
    assert "automatically" in caplog.text


def test_explicit_precedence_is_logged(caplog: pytest.LogCaptureFixture):
    server = FastMCP("root")
    server.add_extension(BundledExtension(optional_method=False))
    with caplog.at_level(logging.DEBUG, logger="fastmcp.server.mixins.extensions"):
        server.add_provider(BundledProvider())
    assert "Using explicitly registered extension" in caplog.text


async def test_extensions_added_to_mounted_child_before_startup_are_discovered():
    child = FastMCP("child")
    root = FastMCP("root")
    root.mount(child)
    child.add_provider(BundledProvider())
    async with Client(root, mode="auto") as client:
        assert EXT_ID in client.server_capabilities.extensions
        root_extension = root._extensions[EXT_ID]
        assert isinstance(root_extension, BundledExtension)
        assert root_extension.lifecycle == ["enter"]
        child_extension = child._extensions[EXT_ID]
        assert isinstance(child_extension, BundledExtension)
        assert child_extension.lifecycle == []
    assert root_extension.lifecycle == ["enter", "exit"]


async def test_extensions_added_to_aggregate_before_startup_are_discovered():
    aggregate = AggregateProvider()
    root = FastMCP("root", providers=[aggregate])
    aggregate.add_provider(BundledProvider())
    async with Client(root, mode="auto") as client:
        assert EXT_ID in client.server_capabilities.extensions


async def test_provider_added_in_user_lifespan_is_discovered():
    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        server.add_provider(BundledProvider())
        yield

    root = FastMCP("root", lifespan=lifespan)
    async with Client(root, mode="auto") as client:
        assert EXT_ID in client.server_capabilities.extensions


async def test_new_extension_after_startup_rejected_without_adding_provider():
    root = FastMCP("root")
    async with Client(root, mode="auto"):
        with pytest.raises(RuntimeError, match="lifespan has already started"):
            root.add_provider(BundledProvider())
    assert len(root.providers) == 1
    assert EXT_ID not in root._extensions


async def test_explicit_override_after_startup_rejected():
    root = FastMCP("root", providers=[BundledProvider()])
    original = root._extensions[EXT_ID]
    async with Client(root, mode="auto"):
        with pytest.raises(RuntimeError, match="lifespan has already started"):
            root.add_extension(BundledExtension(optional_method=False))
    assert root._extensions[EXT_ID] is original


async def test_registration_during_extension_startup_rejected():
    class RegisteringExtension(ServerExtension):
        identifier = "com.example/registering"

        @asynccontextmanager
        async def lifespan(self) -> AsyncIterator[None]:
            with pytest.raises(RuntimeError, match="lifespan has already started"):
                self.server.add_extension(BundledExtension())
            yield

    root = FastMCP("root")
    root.add_extension(RegisteringExtension())
    async with Client(root, mode="auto"):
        assert EXT_ID not in root._extensions


@pytest.mark.parametrize("bundled", [False, True])
def test_extension_auto_registration_requires_opt_in(bundled: bool):
    class ExplicitExtension(ServerExtension):
        identifier = EXT_ID

    root = FastMCP("root")
    if bundled:
        with pytest.raises(ValueError, match="does not allow automatic registration"):
            root.add_provider(BundledProvider(ExplicitExtension()))
        assert len(root.providers) == 1
    else:
        child = FastMCP("child")
        child.add_extension(ExplicitExtension())
        root.mount(child)
    assert EXT_ID not in root._extensions


def test_non_automatic_dependency_can_be_satisfied_explicitly():
    class ExplicitExtension(ServerExtension):
        identifier = EXT_ID

    root = FastMCP("root")
    root.add_extension(ExplicitExtension())
    root.add_provider(BundledProvider(ExplicitExtension()))


def test_tasks_extension_does_not_propagate():
    child = FastMCP("child")
    child.add_extension(TasksExtension())
    root = FastMCP("root")
    root.mount(child)
    assert TasksExtension.identifier not in root._extensions


def test_cloning_clears_binding_and_isolates_mutable_state():
    child = FastMCP("child")
    extension = BundledExtension(optional_method=False)
    child.add_extension(extension)
    clone = extension.clone()
    assert clone.settings() == extension.settings()
    assert isinstance(clone, BundledExtension)
    clone.lifecycle.append("changed")
    assert extension.lifecycle == []
    assert extension.server is child
    with pytest.raises(RuntimeError, match="not bound"):
        _ = clone.server


def test_registration_rejects_sharing_a_bound_instance():
    extension = BundledExtension()
    child = FastMCP("child")
    child.add_extension(extension)
    root = FastMCP("root")
    with pytest.raises(ValueError, match="already bound to another server"):
        root.add_extension(extension)
    assert extension.server is child


def test_unrelated_extension_method_collision_is_rejected():
    class OtherExtension(BundledExtension):
        identifier = "com.example/other"

    root = FastMCP("root")
    root.add_extension(OtherExtension())
    with pytest.raises(
        ValueError, match="method 'bundled/inspect' is already registered"
    ):
        root.add_provider(BundledProvider())
    assert EXT_ID not in root._extensions
    assert len(root.providers) == 1


async def test_failed_bundle_rolls_back_all_extensions_and_handlers():
    class ExplicitExtension(ServerExtension):
        identifier = "com.example/explicit"

    root = FastMCP("root")
    bundle = AggregateProvider(
        [BundledProvider(), BundledProvider(ExplicitExtension())]
    )
    with pytest.raises(ValueError, match="does not allow automatic registration"):
        root.add_provider(bundle)
    assert root._extensions == {}
    assert root._auto_extensions == set()
    assert root._mcp_server.get_request_handler("bundled/inspect") is None
    assert len(root.providers) == 1
    # A corrected attempt must work without stale methods or duplicate IDs.
    root.add_extension(ExplicitExtension())
    root.add_provider(bundle)
    async with Client(root, mode="auto") as client:
        result = await client.session.send_request(
            InspectRequest(params=RequestParams()), InspectResult
        )
        assert result.server == "root"


def test_settings_can_depend_on_receiving_server(caplog: pytest.LogCaptureFixture):
    class ServerAwareExtension(BundledExtension):
        def settings(self) -> dict[str, Any]:
            return {"server": self.server.name}

    child = FastMCP("child", providers=[BundledProvider(ServerAwareExtension())])
    root = FastMCP("root")
    with caplog.at_level(logging.WARNING):
        root.mount(child)
        root.add_provider(BundledProvider(ServerAwareExtension()))
    assert root._extensions[EXT_ID].settings() == {"server": "root"}
    assert "Conflicting" not in caplog.text


def test_custom_clone_can_reconstruct_non_copyable_configuration():
    class LockedExtension(BundledExtension):
        def __init__(self, *, optional_method: bool = True) -> None:
            super().__init__(optional_method=optional_method)
            self.lock = threading.Lock()

        def clone(self) -> ServerExtension:
            return LockedExtension(optional_method=self.optional_method)

    extension = LockedExtension(optional_method=False)
    root = FastMCP("root", providers=[BundledProvider(extension)])
    registered = root._extensions[EXT_ID]
    assert isinstance(registered, LockedExtension)
    assert registered.lock is not extension.lock
    assert registered.settings() == {"optionalMethod": False}


@pytest.mark.parametrize("invalid_clone", ["same_instance", "wrong_identifier"])
def test_invalid_clone_rejected_without_rebinding_source(invalid_clone: str):
    class InvalidCloneExtension(BundledExtension):
        def clone(self) -> ServerExtension:
            if invalid_clone == "same_instance":
                return self
            clone = BundledExtension()
            clone.identifier = "com.example/wrong"
            return clone

    child = FastMCP("child")
    extension = InvalidCloneExtension()
    child.add_extension(extension)
    root = FastMCP("root")
    with pytest.raises(ValueError, match="separate extension with the same identifier"):
        root.mount(child)
    assert extension.server is child
    assert root._extensions == {}


async def test_explicit_child_extension_added_after_mount_is_discovered():
    child = FastMCP("child")
    root = FastMCP("root")
    root.mount(child)
    child.add_extension(BundledExtension(optional_method=False))
    async with Client(root, mode="auto") as client:
        assert client.server_capabilities.extensions[EXT_ID] == {
            "optionalMethod": False
        }


async def test_explicit_root_configuration_wins_over_mounted_child():
    child = FastMCP("child", providers=[BundledProvider()])
    root = FastMCP("root")
    root.mount(child)
    root.add_extension(BundledExtension(optional_method=False))
    async with Client(root, mode="auto") as client:
        assert client.server_capabilities.extensions[EXT_ID] == {
            "optionalMethod": False
        }
    assert child._extensions[EXT_ID].settings() == {"optionalMethod": True}


async def test_mounted_child_lifespan_cannot_introduce_a_new_root_extension():
    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        server.add_provider(BundledProvider())
        yield

    child = FastMCP("child", lifespan=lifespan)
    root = FastMCP("root")
    root.mount(child)
    with pytest.raises(RuntimeError, match="root server 'root' has already started"):
        async with root._lifespan_manager():
            pass
    assert child._extensions == {}
    assert len(child.providers) == 1


async def test_mounted_child_lifespan_can_use_an_existing_root_extension():
    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        server.add_provider(BundledProvider())
        yield

    child = FastMCP("child", lifespan=lifespan)
    root = FastMCP("root", providers=[BundledProvider()])
    root.mount(child)
    async with Client(root, mode="auto") as client:
        assert EXT_ID in client.server_capabilities.extensions


@pytest.mark.parametrize("wrapped", [False, True])
async def test_running_aggregate_rejects_new_extension_without_exposing_components(
    wrapped: bool,
):
    class BundledLocalProvider(LocalProvider):
        def required_extensions(self) -> Sequence[ServerExtension]:
            return [BundledExtension()]

    provider = BundledLocalProvider()

    @provider.tool
    def marker() -> str:
        return "marker"

    aggregate = AggregateProvider()
    composed: Provider = aggregate
    if wrapped:
        composed = AggregateProvider([aggregate.wrap_transform(Namespace("ns"))])
    root = FastMCP("root", providers=[composed])
    async with Client(root, mode="auto") as client:
        with pytest.raises(RuntimeError, match="unavailable in a running server"):
            aggregate.add_provider(provider)
        assert await client.list_tools() == []
        assert root._extensions == {}
    assert aggregate.providers == []


async def test_running_aggregate_accepts_bundle_supported_by_root():
    aggregate = AggregateProvider()
    root = FastMCP("root", providers=[aggregate])
    root.add_extension(BundledExtension())
    async with Client(root, mode="auto"):
        aggregate.add_provider(BundledProvider())
    assert len(aggregate.providers) == 1


async def test_running_aggregate_accepts_components_without_new_extensions():
    aggregate = AggregateProvider()
    root = FastMCP("root", providers=[aggregate])
    provider = LocalProvider()

    @provider.tool
    def marker() -> str:
        return "marker"

    async with Client(root, mode="auto") as client:
        aggregate.add_provider(provider)
        assert [tool.name for tool in await client.list_tools()] == ["marker"]


async def test_shared_aggregate_checks_every_active_server_and_releases_scopes():
    aggregate = AggregateProvider()
    first = FastMCP("first", providers=[aggregate])
    first.add_extension(BundledExtension())
    second = FastMCP("second", providers=[aggregate])
    async with Client(first, mode="auto"):
        async with Client(second, mode="auto"):
            with pytest.raises(RuntimeError, match="unavailable in a running server"):
                aggregate.add_provider(BundledProvider())
        aggregate.add_provider(BundledProvider())
    assert aggregate._extension_scopes == []


@pytest.mark.parametrize("enabled", [True, False])
async def test_propagated_interceptor_runs_once_with_root_configuration(enabled: bool):
    child = FastMCP("child", providers=[BundledProvider(AuditExtension())])

    @child.tool
    def marker() -> str:
        return "marker"

    middle = FastMCP("middle")
    middle.mount(child, namespace="child", tool_names={"marker": "renamed"})
    root = FastMCP("root")
    root.mount(middle, namespace="middle")
    explicit = AuditExtension(enabled=enabled)
    root.add_extension(explicit)
    child_extension = child._extensions[EXT_ID]
    middle_extension = middle._extensions[EXT_ID]
    assert isinstance(child_extension, AuditExtension)
    assert isinstance(middle_extension, AuditExtension)
    async with Client(root, mode="auto") as client:
        await client.call_tool("middle_child_renamed")
    assert explicit.calls == (["middle_child_renamed"] if enabled else [])
    assert child_extension.calls == []
    assert middle_extension.calls == []
    async with Client(child, mode="auto") as client:
        await client.call_tool("marker")
    assert child_extension.calls == ["marker"]


async def test_delegation_preserves_programmatic_child_calls():
    child = FastMCP("child", providers=[BundledProvider(AuditExtension())])

    @child.tool
    def inner() -> str:
        return "inner"

    @child.tool
    async def outer() -> str:
        await child.call_tool("inner")
        return "outer"

    root = FastMCP("root")
    root.mount(child)
    async with Client(root, mode="auto") as client:
        await client.call_tool("outer")
    root_extension = root._extensions[EXT_ID]
    child_extension = child._extensions[EXT_ID]
    assert isinstance(root_extension, AuditExtension)
    assert isinstance(child_extension, AuditExtension)
    assert root_extension.calls == ["outer"]
    assert child_extension.calls == ["inner"]


async def test_non_propagating_interceptors_keep_server_local_behavior():
    class LocalAuditExtension(AuditExtension):
        auto_register = False

    child = FastMCP("child")
    child_extension = LocalAuditExtension()
    child.add_extension(child_extension)

    @child.tool
    def marker() -> str:
        return "marker"

    root = FastMCP("root")
    root_extension = LocalAuditExtension()
    root.add_extension(root_extension)
    root.mount(child)
    async with Client(root, mode="auto") as client:
        await client.call_tool("marker")
    assert root_extension.calls == ["marker"]
    assert child_extension.calls == ["marker"]


async def test_nested_child_setup_preserves_ancestor_interceptor_configuration():
    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        server.add_provider(BundledProvider(AuditExtension()))
        yield

    child = FastMCP("child", lifespan=lifespan)

    @child.tool
    def marker() -> str:
        return "marker"

    middle = FastMCP("middle")
    middle.mount(child)
    root = FastMCP("root")
    root.mount(middle)
    root.add_extension(AuditExtension(enabled=False))
    async with Client(root, mode="auto") as client:
        await client.call_tool("marker")
        child_extension = child._extensions[EXT_ID]
        assert isinstance(child_extension, AuditExtension)
        assert child_extension.calls == []
    assert middle._extensions == {}


async def test_delegation_preserves_programmatic_calls_from_child_middleware():
    child = FastMCP("child", providers=[BundledProvider(AuditExtension())])

    @child.tool
    def inner() -> str:
        return "inner"

    @child.tool
    def outer() -> str:
        return "outer"

    class CallingMiddleware(Middleware):
        async def on_call_tool(
            self,
            context: MiddlewareContext[mcp_types.CallToolRequestParams],
            call_next: CallNext[mcp_types.CallToolRequestParams, Any],
        ) -> Any:
            if context.message.name == "outer":
                await child.call_tool("inner")
            return await call_next(context)

    child.add_middleware(CallingMiddleware())
    root = FastMCP("root")
    root.mount(child)
    async with Client(root, mode="auto") as client:
        await client.call_tool("outer")
    root_extension = root._extensions[EXT_ID]
    child_extension = child._extensions[EXT_ID]
    assert isinstance(root_extension, AuditExtension)
    assert isinstance(child_extension, AuditExtension)
    assert root_extension.calls == ["outer"]
    assert child_extension.calls == ["inner"]


async def test_aggregate_setup_cannot_introduce_a_new_root_extension():
    aggregate = AggregateProvider()

    class SetupProvider(Provider):
        @asynccontextmanager
        async def lifespan(self) -> AsyncIterator[None]:
            aggregate.add_provider(BundledProvider())
            yield

    aggregate.add_provider(SetupProvider())
    root = FastMCP("root", providers=[aggregate])
    with pytest.raises(RuntimeError, match="unavailable in a running server"):
        async with root._lifespan_manager():
            pass
    assert root._extensions == {}
    assert len(aggregate.providers) == 1


class MutatingProvider(Provider):
    """A provider whose lifespan adds a bundled provider to a later sibling."""

    def __init__(self, target: FastMCP | AggregateProvider) -> None:
        super().__init__()
        self.target = target

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        self.target.add_provider(BundledProvider())
        yield


@pytest.mark.parametrize("target_kind", ["mounted_server", "aggregate"])
async def test_lifespan_cannot_introduce_a_root_extension_via_a_later_sibling(
    target_kind: Literal["mounted_server", "aggregate"],
):
    target = (
        FastMCP("later") if target_kind == "mounted_server" else AggregateProvider()
    )
    root = FastMCP("root", providers=[MutatingProvider(target)])
    if isinstance(target, FastMCP):
        root.mount(target)
    else:
        root.add_provider(target)

    with pytest.raises(RuntimeError, match="lifespan has already started"):
        async with root._lifespan_manager():
            pass
    assert root._extensions == {}


async def test_lifespan_can_add_a_bundle_the_root_already_supports_to_a_later_sibling():
    later = FastMCP("later")
    root = FastMCP("root", providers=[BundledProvider(), MutatingProvider(later)])
    root.mount(later)

    async with Client(root, mode="auto") as client:
        assert EXT_ID in client.server_capabilities.extensions


@pytest.mark.parametrize("target_kind", ["mounted_server", "aggregate"])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_aggregate_startup_rejects_new_extension_on_a_later_descendant(
    target_kind: Literal["mounted_server", "aggregate"], wrapped: bool
):
    later = FastMCP("later") if target_kind == "mounted_server" else AggregateProvider()
    aggregate = AggregateProvider([MutatingProvider(later)])
    aggregate.add_provider(later)
    composed: Provider = aggregate
    if wrapped:
        composed = AggregateProvider([aggregate.wrap_transform(Namespace("ns"))])
    root = FastMCP("root", providers=[composed])

    with pytest.raises(RuntimeError, match="unavailable in a running server"):
        async with root._lifespan_manager():
            pass
    assert root._extensions == {}
    assert aggregate._extension_scopes == []


@pytest.mark.parametrize("target_kind", ["mounted_server", "aggregate"])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_aggregate_startup_accepts_supported_bundle_on_a_later_descendant(
    target_kind: Literal["mounted_server", "aggregate"], wrapped: bool
):
    later = FastMCP("later") if target_kind == "mounted_server" else AggregateProvider()
    aggregate = AggregateProvider([MutatingProvider(later)])
    aggregate.add_provider(later)
    composed: Provider = aggregate
    if wrapped:
        composed = AggregateProvider([aggregate.wrap_transform(Namespace("ns"))])
    root = FastMCP("root", providers=[BundledProvider(), composed])

    async with Client(root, mode="auto") as client:
        result = await client.session.send_request(
            InspectRequest(params=RequestParams()), InspectResult
        )
        assert result.server == "root"
        extension = root._extensions[EXT_ID]
        assert isinstance(extension, BundledExtension)
        assert extension.lifecycle == ["enter"]
    assert extension.lifecycle == ["enter", "exit"]
    assert aggregate._extension_scopes == []
