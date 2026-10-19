"""Shared dependency scopes must not depend on optional task dependencies."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from uncalled_for import SharedContext

from fastmcp import Client, FastMCP
from fastmcp.dependencies import Shared
from fastmcp.server.context import Context


@pytest.fixture(params=[False, True], ids=["without-docket", "with-docket"])
def docket_available(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "fastmcp.server.dependencies.is_docket_available", lambda: request.param
    )


@pytest.mark.usefixtures("docket_available")
async def test_client_shares_dependency_until_shutdown():
    server = FastMCP("shared-lifecycle")
    entered = 0
    exited = 0

    @asynccontextmanager
    async def connection() -> AsyncGenerator[str, None]:
        nonlocal entered, exited
        entered += 1
        try:
            yield f"connection-{entered}"
        finally:
            exited += 1

    @server.tool()
    async def read_connection(value: str = Shared(connection)) -> str:
        return value

    for lifecycle in (1, 2):
        async with Client(server) as client:
            for _ in range(2):
                result = await client.call_tool("read_connection", {})
                assert result.data == f"connection-{lifecycle}"
            assert entered == lifecycle
            assert exited == lifecycle - 1
        assert exited == lifecycle


@pytest.mark.usefixtures("docket_available")
async def test_direct_call_owns_temporary_shared_scope():
    server = FastMCP("direct-shared")
    entered = 0
    exited = 0

    @asynccontextmanager
    async def connection() -> AsyncGenerator[str, None]:
        nonlocal entered, exited
        entered += 1
        try:
            yield f"connection-{entered}"
        finally:
            exited += 1

    @server.tool()
    async def read_connection(value: str = Shared(connection)) -> str:
        return value

    for call in (1, 2):
        result = await server.call_tool("read_connection", {})
        assert result.structured_content == {"result": f"connection-{call}"}
        assert entered == exited == call
        with pytest.raises(LookupError):
            SharedContext.resolved.get()


@pytest.mark.usefixtures("docket_available")
async def test_context_preserves_explicit_shared_scope():
    server = FastMCP("explicit-shared")
    async with SharedContext():
        resolved = SharedContext.resolved.get()
        async with Context(server):
            assert SharedContext.resolved.get() is resolved
            async with Context(server):
                assert SharedContext.resolved.get() is resolved
            assert SharedContext.resolved.get() is resolved
        assert SharedContext.resolved.get() is resolved
