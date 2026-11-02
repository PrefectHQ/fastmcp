import asyncio
from unittest.mock import Mock

import anyio
import pytest
from mcp_types import TextContent

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import ProxyClient
from tests.client.test_stdio import (
    MINIMAL_STDIO_SERVER,
    gc_collect_harder,
    wait_for_process_exit,
)

pytestmark = pytest.mark.timeout(15)


async def test_shared_client_keeps_subprocess_until_last_exit():
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    try:
        async with Client(transport) as first:
            async with Client(transport) as second:
                pid = (await second.call_tool("pid")).data
                assert (await first.call_tool("pid")).data == pid
            assert (await first.call_tool("pid")).data == pid
        await wait_for_process_exit(pid)
    finally:
        await transport.close()


@pytest.mark.parametrize("cancellation", ["scope", "native"])
async def test_cancelled_shared_client_and_gc(cancellation):
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    ready = asyncio.Event()
    abandoned_runner = None

    async def abandoned_call():
        nonlocal abandoned_runner
        async with Client(transport) as client:
            abandoned_runner = client._session_state.session_task
            await client.call_tool("pid")
            ready.set()
            await anyio.sleep_forever()

    try:
        async with Client(transport) as survivor:
            pid = (await survivor.call_tool("pid")).data
            if cancellation == "scope":

                async def cancel_when_ready(scope):
                    await ready.wait()
                    scope.cancel()

                scope = anyio.CancelScope()
                trigger = asyncio.create_task(cancel_when_ready(scope))
                with scope:
                    await abandoned_call()
                await trigger
                assert scope.cancelled_caught
            else:
                task = asyncio.create_task(abandoned_call())
                await ready.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                del task

            # Client context cleanup can finish in its independent stopper task.
            with anyio.fail_after(3):
                while transport._active_sessions > 1:
                    await anyio.sleep(0.01)
            assert abandoned_runner is not None
            await asyncio.wait({abandoned_runner}, timeout=3)
            assert abandoned_runner.done()
            gc_collect_harder()
            assert (await survivor.call_tool("pid")).data == pid

        await wait_for_process_exit(pid)
        gc_collect_harder()
        with anyio.fail_after(3):
            async with Client(transport) as client:
                replacement_pid = (await client.call_tool("pid")).data
                assert replacement_pid != pid
            await wait_for_process_exit(replacement_pid)
            proxy = create_proxy(transport)
            result = await proxy.call_tool("pid")
            content = result.content[0]
            assert isinstance(content, TextContent)
            await wait_for_process_exit(int(content.text))
    finally:
        await transport.close()


async def test_new_client_waits_for_stopping_connection(monkeypatch):
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    stopping = asyncio.Event()
    finish_stop = asyncio.Event()
    new_ready = asyncio.Event()
    original_wait = transport._stop_event.wait

    async def delayed_stop():
        await original_wait()
        stopping.set()
        await finish_stop.wait()

    stop_event = Mock(wraps=transport._stop_event)
    stop_event.wait = delayed_stop
    monkeypatch.setattr(transport, "_stop_event", stop_event)
    first = Client(transport)
    await first.__aenter__()
    old_pid = (await first.call_tool("pid")).data
    exiting = asyncio.create_task(first.__aexit__(None, None, None))
    await stopping.wait()

    async def reconnect():
        async with Client(transport) as client:
            new_ready.set()
            return (await client.call_tool("pid")).data

    entering = asyncio.create_task(reconnect())
    try:
        with anyio.fail_after(3):
            while not transport._connect_lock.locked() and not new_ready.is_set():
                await asyncio.sleep(0)
        assert not new_ready.is_set()
        finish_stop.set()
        await exiting
        new_pid = await entering
        assert new_pid != old_pid
        await wait_for_process_exit(old_pid)
        await wait_for_process_exit(new_pid)
    finally:
        finish_stop.set()
        await asyncio.gather(exiting, entering, return_exceptions=True)
        await transport.close()


async def test_late_disconnect_does_not_clear_replacement(monkeypatch):
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    await transport.connect()
    old_task = transport._connect_task
    assert old_task is not None
    late_waiter = asyncio.Event()
    release_waiter = asyncio.Event()
    original_wait = asyncio.wait
    original_session = transport._session

    async def delay_first_wait(tasks, **kwargs):
        result = await original_wait(tasks, **kwargs)
        if asyncio.current_task() is disconnecting:
            late_waiter.set()
            await release_waiter.wait()
        return result

    # Only delay one disconnect waiter after the real subprocess has stopped.
    monkeypatch.setattr(asyncio, "wait", delay_first_wait)
    disconnecting = asyncio.create_task(transport.disconnect())
    try:
        await late_waiter.wait()
        async with Client(transport) as client:
            new_task = transport._connect_task
            new_stop = transport._stop_event
            new_ready = transport._ready_event
            new_session = transport._session
            assert new_task is not old_task
            assert transport._session is not original_session
            release_waiter.set()
            await disconnecting
            assert transport._connect_task is new_task
            assert transport._stop_event is new_stop
            assert transport._ready_event is new_ready
            assert transport._session is new_session
            pid = (await client.call_tool("pid")).data
        await wait_for_process_exit(pid)
    finally:
        release_waiter.set()
        await disconnecting
        await transport.close()


async def test_collected_transport_context_releases_shared_hold():
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    try:
        async with Client(transport) as survivor:
            pid = (await survivor.call_tool("pid")).data
            abandoned = transport.connect_session()
            await abandoned.__aenter__()
            del abandoned
            gc_collect_harder()
            with anyio.fail_after(3):
                while transport._active_sessions > 1:
                    await anyio.sleep(0.01)
            assert (await survivor.call_tool("pid")).data == pid
        await wait_for_process_exit(pid)
    finally:
        await transport.close()


@pytest.mark.parametrize("entry", ["connect_session", "client"])
async def test_cancelled_scope_stops_shared_subprocess_after_last_exit(entry):
    """Every session sharing the subprocess exits under one cancelled scope.

    The last exit runs during cancellation, so it must stop the subprocess
    without waiting on anything the cancellation can interrupt, and the next
    connection must not be left behind a half-reset transport.
    """
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    pid: int | None = None
    try:
        with anyio.CancelScope() as scope:
            if entry == "connect_session":
                async with transport.connect_session() as first:
                    async with transport.connect_session() as second:
                        assert first is second
                        result = await second.call_tool("pid", {})
                        content = result.content[0]
                        assert isinstance(content, TextContent)
                        pid = int(content.text)
                        scope.cancel()
                        await anyio.sleep_forever()
            else:
                async with Client(transport) as first:
                    async with Client(transport) as second:
                        pid = (await second.call_tool("pid")).data
                        assert (await first.call_tool("pid")).data == pid
                        scope.cancel()
                        await anyio.sleep_forever()
        assert scope.cancelled_caught
        await wait_for_process_exit(pid)

        with anyio.fail_after(3):
            async with Client(transport) as client:
                assert (await client.call_tool("pid")).data != pid
    finally:
        await transport.close()


async def test_proxy_request_leaves_concurrent_backend_session_running():
    """`create_proxy` builds a fresh backend client per request on the shared
    transport, so one request finishing must not stop the subprocess under
    another request that is still connected."""
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    proxy = create_proxy(transport)
    try:
        async with ProxyClient(transport) as in_flight:
            pid = (await in_flight.call_tool("pid")).data
            result = await proxy.call_tool("pid")
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert int(content.text) == pid
            assert (await in_flight.call_tool("pid")).data == pid
        await wait_for_process_exit(pid)
    finally:
        await transport.close()


# The unshared case is the last session out, so its cleanup must await the
# subprocess's exit inside a coroutine that is being closed and cannot await.
# The stop is already signalled by then; the warning is the destroyed runner
# reporting that it could not finish unwinding.
@pytest.mark.filterwarnings(
    "ignore:Exception ignored in.*Client._session_runner"
    ":pytest.PytestUnraisableExceptionWarning"
)
@pytest.mark.parametrize("shared", [False, True])
async def test_collected_abandoned_client_does_not_wedge_transport(shared):
    """A Client entered, never exited, and then garbage-collected has its
    pending session task destroyed, so its session cleanup runs in a closing
    coroutine. That cleanup must not take the connect lock: a lock acquired
    there is never released, so the subprocess outlives every other client and
    every later Client, including `create_proxy` requests, hangs."""
    transport = PythonStdioTransport(MINIMAL_STDIO_SERVER, keep_alive=False)
    survivor = Client(transport)
    try:
        if shared:
            await survivor.__aenter__()
        abandoned = Client(transport)
        await abandoned.__aenter__()
        pid = (await abandoned.call_tool("pid")).data
        del abandoned
        gc_collect_harder()
        await anyio.sleep(0.1)
        gc_collect_harder()

        if shared:
            assert (await survivor.call_tool("pid")).data == pid
            await survivor.__aexit__(None, None, None)
        await wait_for_process_exit(pid)

        with anyio.fail_after(3):
            async with Client(transport) as client:
                assert (await client.call_tool("pid")).data != pid
            result = await create_proxy(transport).call_tool("pid")
            content = result.content[0]
            assert isinstance(content, TextContent)
            await wait_for_process_exit(int(content.text))
    finally:
        if survivor.is_connected():
            await survivor.__aexit__(None, None, None)
        await transport.close()
