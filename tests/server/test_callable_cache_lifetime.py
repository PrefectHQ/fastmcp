"""Callable caches must not outlive the user functions they accelerate."""

import gc
import weakref
from collections.abc import Callable
from functools import partial, wraps

import cloudpickle
import pytest
from uncalled_for import introspection

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.dependencies import without_injected_parameters
from fastmcp.server.sessions import session_id_parameter_names
from fastmcp.tools.function_tool import _wrap_body_errors
from fastmcp.utilities.types import get_cached_typeadapter


class Payload:
    def __init__(self) -> None:
        self.items = list(range(100))


@pytest.fixture
def external_introspection_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    # uncalled-for #21 independently retains callables. Isolate its two caches
    # so these regressions can verify FastMCP's ownership without changing the
    # dependency's process-wide cache objects in production.
    monkeypatch.setattr(introspection, "_signature_cache", {})
    monkeypatch.setattr(introspection, "_parameter_cache", {})


def release_external_introspection() -> None:
    introspection._signature_cache.clear()
    introspection._parameter_cache.clear()


@pytest.mark.usefixtures("external_introspection_caches")
@pytest.mark.parametrize(
    "build",
    [
        get_cached_typeadapter,
        without_injected_parameters,
        _wrap_body_errors,
        session_id_parameter_names,
    ],
    ids=["type-adapter", "dependencies", "body-errors", "session-id"],
)
def test_callable_cache_does_not_retain_a_dropped_closure(
    build: Callable[..., object],
) -> None:
    def populate() -> tuple[weakref.ReferenceType, weakref.ReferenceType]:
        payload = Payload()

        def tool(query: str) -> int:
            return len(payload.items)

        build(tool)
        return weakref.ref(tool), weakref.ref(payload)

    tool_ref, payload_ref = populate()
    release_external_introspection()
    gc.collect()

    assert tool_ref() is None
    assert payload_ref() is None


@pytest.mark.usefixtures("external_introspection_caches")
async def test_dropped_servers_leave_no_fastmcp_owned_tool_closures() -> None:
    async def build_and_call() -> tuple[weakref.ReferenceType, weakref.ReferenceType]:
        server = FastMCP("temporary")
        payload = Payload()

        def search(query: str) -> int:
            return len(payload.items)

        server.tool(search)
        async with Client(server) as client:
            result = await client.call_tool("search", {"query": "q"})
            assert result.data == 100
        return weakref.ref(search), weakref.ref(payload)

    refs = [await build_and_call() for _ in range(3)]
    release_external_introspection()
    gc.collect()

    assert all(tool() is None and payload() is None for tool, payload in refs)


@pytest.mark.usefixtures("external_introspection_caches")
def test_live_callable_keeps_its_cached_adapter() -> None:
    def tool(value: int) -> int:
        return value + 1

    adapter = get_cached_typeadapter(tool)
    assert get_cached_typeadapter(tool) is adapter
    assert adapter.validate_python({"value": 2}) == 3
    gc.collect()
    assert get_cached_typeadapter(tool) is adapter
    assert get_cached_typeadapter(int) is get_cached_typeadapter(int)


def test_wrapped_function_does_not_reuse_the_source_functions_cache() -> None:
    def source() -> int:
        return 1

    original = _wrap_body_errors(source)

    @wraps(source)
    def wrapped() -> int:
        return 2

    distinct = _wrap_body_errors(wrapped)
    assert distinct is not original
    assert original() == 1
    assert distinct() == 2
    assert _wrap_body_errors(wrapped) is distinct


@pytest.mark.usefixtures("external_introspection_caches")
def test_dependency_wrapper_caches_each_thread_option_separately() -> None:
    def tool(value: int, ctx: Context) -> int:
        return value

    threaded = without_injected_parameters(tool, run_in_thread=True)
    inline = without_injected_parameters(tool, run_in_thread=False)
    assert threaded is not inline
    assert without_injected_parameters(tool, run_in_thread=True) is threaded
    assert without_injected_parameters(fn=tool, run_in_thread=False) is inline


@pytest.mark.usefixtures("external_introspection_caches")
def test_bound_method_cache_is_owned_by_its_instance() -> None:
    class Receiver:
        def __init__(self, value: int) -> None:
            self.value = value

        def tool(self, offset: int) -> int:
            return self.value + offset

    live = Receiver(10)
    adapter = get_cached_typeadapter(live.tool)
    assert get_cached_typeadapter(live.tool) is adapter

    def populate() -> weakref.ReferenceType:
        dropped = Receiver(20)
        other = get_cached_typeadapter(dropped.tool)
        assert other is not adapter
        assert other.validate_python({"offset": 1}) == 21
        _wrap_body_errors(dropped.tool)
        without_injected_parameters(dropped.tool)
        session_id_parameter_names(dropped.tool)
        return weakref.ref(dropped)

    dropped_ref = populate()
    release_external_introspection()
    gc.collect()
    assert dropped_ref() is None
    assert get_cached_typeadapter(live.tool) is adapter
    assert adapter.validate_python({"offset": 1}) == 11


@pytest.mark.usefixtures("external_introspection_caches")
def test_method_without_instance_namespace_is_not_retained() -> None:
    class Receiver:
        __slots__ = ("__weakref__",)

        def tool(self, offset: int) -> int:
            return offset

    def populate() -> weakref.ReferenceType:
        receiver = Receiver()
        adapter = get_cached_typeadapter(receiver.tool)
        assert adapter.validate_python({"offset": 2}) == 2
        _wrap_body_errors(receiver.tool)
        without_injected_parameters(receiver.tool)
        session_id_parameter_names(receiver.tool)
        return weakref.ref(receiver)

    receiver_ref = populate()
    release_external_introspection()
    gc.collect()
    assert receiver_ref() is None


def test_partial_adapter_is_reused_without_retaining_its_bound_payload() -> None:
    def tool(payload: Payload, offset: int) -> int:
        return len(payload.items) + offset

    def populate() -> tuple[weakref.ReferenceType, weakref.ReferenceType]:
        payload = Payload()
        bound = partial(tool, payload)
        adapter = get_cached_typeadapter(bound)
        assert get_cached_typeadapter(bound) is adapter
        assert adapter.validate_python({"offset": 2}) == 102
        return weakref.ref(bound), weakref.ref(payload)

    bound_ref, payload_ref = populate()
    gc.collect()
    assert bound_ref() is None
    assert payload_ref() is None


def test_callable_cache_is_not_serialized_with_a_task_function() -> None:
    def tool(value: int) -> int:
        return value + 1

    get_cached_typeadapter(tool)
    restored = cloudpickle.loads(cloudpickle.dumps(tool))
    assert restored(2) == 3
    assert get_cached_typeadapter(restored).validate_python({"value": 2}) == 3
    assert get_cached_typeadapter(restored) is not get_cached_typeadapter(tool)
