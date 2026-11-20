"""Concurrent lifespan startup, ownership, and cancellation regressions."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

import anyio
import pytest
from exceptiongroup import ExceptionGroup

import fastmcp
from fastmcp import Client, FastMCP


async def test_concurrent_lifespan_waits_for_complete_startup():
    starting = asyncio.Event()
    release = asyncio.Event()
    entrants = asyncio.Event()
    entries = 0
    exits = 0

    @asynccontextmanager
    async def lifespan(server):
        nonlocal entries, exits
        entries += 1
        starting.set()
        await release.wait()
        try:
            yield {"ready": True}
        finally:
            exits += 1

    server = FastMCP(lifespan=lifespan)

    async def enter():
        async with server._lifespan_manager():
            assert server._lifespan_result == {"ready": True}
            await entrants.wait()

    first = asyncio.create_task(enter())
    await starting.wait()
    second = asyncio.create_task(enter())
    await asyncio.sleep(0)
    assert not first.done()
    assert not second.done()
    assert entries == 1
    release.set()
    entrants.set()
    await asyncio.gather(first, second)
    assert entries == exits == 1
    assert server._lifespan_ref_count == 0


async def test_first_entrant_can_exit_before_last_with_task_bound_context():
    user_context = ContextVar("lifespan-test", default="outside")
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    first_exited = asyncio.Event()
    events = []

    @asynccontextmanager
    async def lifespan(server):
        token = user_context.set("inside")
        async with anyio.create_task_group():
            events.append("enter")
            try:
                yield {}
            finally:
                await asyncio.sleep(0)
                user_context.reset(token)
                events.append("exit")

    server = FastMCP(lifespan=lifespan)

    async def first():
        async with server._lifespan_manager():
            assert user_context.get() == "inside"
            first_entered.set()
            await second_entered.wait()
        assert user_context.get() == "outside"
        assert events == ["enter"]
        first_exited.set()

    async def second():
        await first_entered.wait()
        async with server._lifespan_manager():
            assert user_context.get() == "inside"
            second_entered.set()
            await first_exited.wait()
        assert user_context.get() == "outside"
        assert events == ["enter", "exit"]

    await asyncio.gather(first(), second())


async def test_lifespan_can_retry_after_startup_failure():
    entries = 0

    @asynccontextmanager
    async def lifespan(server):
        nonlocal entries
        entries += 1
        if entries == 1:
            raise ValueError("startup failed")
        yield {}

    server = FastMCP(lifespan=lifespan)
    with pytest.raises(ValueError, match="startup failed"):
        async with server._lifespan_manager():
            pytest.fail("Failed startup must not admit an entrant")
    assert server._lifespan_ref_count == 0
    assert server._lifespan_task is None
    async with server._lifespan_manager():
        assert server._lifespan_result_set
    assert entries == 2


async def test_cancelled_startup_completes_asynchronous_cleanup():
    starting = asyncio.Event()
    cleaned_up = asyncio.Event()

    @asynccontextmanager
    async def lifespan(server):
        try:
            starting.set()
            await asyncio.Event().wait()
            yield {}
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    server = FastMCP(lifespan=lifespan)

    async def enter():
        async with server._lifespan_manager():
            pytest.fail("Cancelled startup must not admit an entrant")

    entrant = asyncio.create_task(enter())
    await starting.wait()
    owner = server._lifespan_task
    assert owner is not None
    entrant.cancel()
    with pytest.raises(asyncio.CancelledError):
        await entrant
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert cleaned_up.is_set()
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0
    assert not server._started.is_set()


async def test_cancelled_final_exit_keeps_cleanup_owned():
    cleaning_up = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned_up = asyncio.Event()

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            cleaning_up.set()
            await release_cleanup.wait()
            cleaned_up.set()

    server = FastMCP(lifespan=lifespan)

    async def enter():
        async with server._lifespan_manager():
            pass

    entrant = asyncio.create_task(enter())
    await cleaning_up.wait()
    owner = server._lifespan_task
    assert owner is not None
    entrant.cancel()
    with pytest.raises(asyncio.CancelledError):
        await entrant
    assert not owner.done()
    assert not cleaned_up.is_set()
    release_cleanup.set()
    await owner
    assert cleaned_up.is_set()
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0


async def test_client_disconnect_deadline_preserves_closing_generation(monkeypatch):
    monkeypatch.setattr(fastmcp.settings, "client_disconnect_timeout", 0.05)
    cleaning_up = asyncio.Event()
    release_cleanup = asyncio.Event()
    user_context = ContextVar("bounded-lifespan", default="outside")
    events = []

    @asynccontextmanager
    async def lifespan(server):
        token = user_context.set("inside")
        events.append("enter")
        try:
            yield {}
        finally:
            cleaning_up.set()
            await release_cleanup.wait()
            user_context.reset(token)
            assert user_context.get() == "outside"
            events.append("exit")

    server = FastMCP(lifespan=lifespan)

    async def connect():
        async with Client(server) as client:
            await client.list_tools()

    disconnecting = asyncio.create_task(connect())
    await cleaning_up.wait()
    owner = server._lifespan_task
    assert owner is not None
    try:
        await asyncio.wait_for(disconnecting, timeout=0.5)
        assert not owner.done()
        assert server._lifespan_task is owner
        assert events == ["enter"]
        reconnecting = asyncio.create_task(connect())
        await asyncio.sleep(0)
        assert events == ["enter"]
        assert not reconnecting.done()
    finally:
        release_cleanup.set()
        await owner
    await reconnecting
    assert events == ["enter", "exit", "enter", "exit"]
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0


async def test_background_lifespan_failure_interrupts_resource_users():
    failing = asyncio.Event()
    admitted = asyncio.Event()
    entries = 0
    exits = 0

    async def fail():
        await failing.wait()
        raise ValueError("background lifespan failure")

    @asynccontextmanager
    async def lifespan(server):
        nonlocal entries, exits
        entries += 1
        async with anyio.create_task_group() as group:
            if entries == 1:
                group.start_soon(fail)
            try:
                yield {}
            finally:
                exits += 1

    server = FastMCP(lifespan=lifespan)

    async def enter():
        async with server._lifespan_manager():
            admitted.set()
            await asyncio.Event().wait()

    entrant = asyncio.create_task(enter())
    await admitted.wait()
    failing.set()
    with pytest.raises(ExceptionGroup, match="unhandled errors") as error:
        await entrant
    assert isinstance(error.value.exceptions[0], ValueError)
    assert str(error.value.exceptions[0]) == "background lifespan failure"
    assert exits == 1
    assert server._lifespan_ref_count == 0
    assert not server._lifespan_entrants
    async with server._lifespan_manager():
        assert entries == 2
    assert exits == 2


async def test_http_cancelled_between_lifespan_startup_and_socket_binding(monkeypatch):
    binding = asyncio.Event()
    events = []

    @asynccontextmanager
    async def lifespan(server):
        events.append("enter")
        try:
            yield {}
        finally:
            events.append("exit")

    async def create_server(*args, **kwargs):
        binding.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio.get_running_loop(), "create_server", create_server)
    server = FastMCP(lifespan=lifespan)
    serving = asyncio.create_task(
        server.run_http_async(host="127.0.0.1", port=0, show_banner=False)
    )
    await binding.wait()
    assert server._lifespan_ref_count == 2
    serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await serving
    assert events == ["enter", "exit"]
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0


async def test_failed_generation_rejects_new_entrants_during_cleanup():
    admitted = asyncio.Event()
    fail_now = asyncio.Event()
    cleaning_up = asyncio.Event()
    release_cleanup = asyncio.Event()
    interrupted = asyncio.Event()
    release_entrant = asyncio.Event()
    new_admitted = asyncio.Event()
    entries = 0

    async def fail():
        await fail_now.wait()
        raise ValueError("background failure")

    @asynccontextmanager
    async def lifespan(server):
        nonlocal entries
        entries += 1
        async with anyio.create_task_group() as group:
            group.start_soon(fail)
            try:
                yield {}
            finally:
                with anyio.CancelScope(shield=True):
                    cleaning_up.set()
                    await release_cleanup.wait()

    server = FastMCP(lifespan=lifespan)

    async def first():
        async with server._lifespan_manager():
            admitted.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                interrupted.set()
                await release_entrant.wait()

    async def another():
        async with server._lifespan_manager():
            new_admitted.set()

    entrant = asyncio.create_task(first())
    await admitted.wait()
    fail_now.set()
    await cleaning_up.wait()
    await interrupted.wait()
    another_entrant = asyncio.create_task(another())
    try:
        await asyncio.sleep(0)
        assert not new_admitted.is_set()
        assert not another_entrant.done()
        release_cleanup.set()
        with pytest.raises(ExceptionGroup):
            await another_entrant
        assert entries == 1
        assert server._lifespan_ref_count == 1
    finally:
        release_cleanup.set()
        release_entrant.set()
        with pytest.raises(ExceptionGroup):
            await entrant
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0


async def test_lifespan_owner_cancelled_before_first_step_can_retry(monkeypatch):
    server = FastMCP()
    create_task = asyncio.create_task

    def cancel_before_start(coroutine):
        task = create_task(coroutine)
        task.cancel()
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        return task

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", cancel_before_start)
        with pytest.raises(asyncio.CancelledError):
            async with server._lifespan_manager():
                pytest.fail("Cancelled startup must not admit an entrant")
    await asyncio.sleep(0)
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0
    async with server._lifespan_manager():
        assert server._lifespan_result_set


async def test_owner_failure_before_starting_caller_resumes_is_propagated(monkeypatch):
    cleanup = asyncio.Event()
    fail_now = asyncio.Event()
    shield = asyncio.shield

    async def delayed_ready(awaitable):
        result = await shield(awaitable)
        if not isinstance(awaitable, asyncio.Task):
            # Model a caller that resumes after its owner's background failure.
            fail_now.set()
            await cleanup.wait()
        return result

    async def fail():
        await fail_now.wait()
        raise ValueError("failure after readiness")

    @asynccontextmanager
    async def lifespan(server):
        async with anyio.create_task_group() as group:
            group.start_soon(fail)
            try:
                yield {}
            finally:
                cleanup.set()

    server = FastMCP(lifespan=lifespan)
    monkeypatch.setattr(asyncio, "shield", delayed_ready)
    with pytest.raises(ExceptionGroup) as error:
        async with server._lifespan_manager():
            pytest.fail("A failed owner must not admit its starting caller")
    assert isinstance(error.value.exceptions[0], ValueError)
    assert str(error.value.exceptions[0]) == "failure after readiness"
    assert server._lifespan_task is None
    assert server._lifespan_ref_count == 0
