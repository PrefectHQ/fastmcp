import anyio
import httpx2

from fastmcp.client.oauth_callback import (
    OAuthCallbackResult,
    create_oauth_callback_server,
)
from fastmcp.utilities.http import find_available_port


async def _wait_until_listening(server) -> None:
    """Poll until the callback server's socket is accepting connections.

    uvicorn sets `Server.started = True` right after it binds and starts
    listening on the socket, before `serve()` moves on to request handling,
    so this is a deterministic readiness signal in place of a fixed sleep.
    """
    with anyio.fail_after(5):
        while not server.started:
            await anyio.sleep(0.001)


async def test_oauth_callback_result_ignores_subsequent_callbacks():
    """Only the first callback should be captured in shared OAuth callback state."""
    port = find_available_port()
    result = OAuthCallbackResult()
    result_ready = anyio.Event()
    server = create_oauth_callback_server(
        port=port,
        result_container=result,
        result_ready=result_ready,
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)

        await _wait_until_listening(server)

        async with httpx2.AsyncClient() as client:
            first = await client.get(
                f"http://127.0.0.1:{port}/callback?code=good&state=s1"
            )
            assert first.status_code == 200

            await result_ready.wait()

            second = await client.get(
                f"http://127.0.0.1:{port}/callback?code=evil&state=s2"
            )
            assert second.status_code == 200

        assert result.error is None
        assert result.code == "good"
        assert result.state == "s1"

        tg.cancel_scope.cancel()


def test_oauth_callback_server_uses_configured_host():
    server = create_oauth_callback_server(port=find_available_port(), host="localhost")

    assert server.config.host == "localhost"


async def test_oauth_callback_result_captures_iss():
    """RFC 9207: the `iss` query parameter must survive from the raw callback
    request through to `OAuthCallbackResult`, the same as `code` and `state`.

    OAuthProxy advertises `authorization_response_iss_parameter_supported` and
    includes `iss` on every authorization redirect. If the callback server's
    query-parsing chain (CallbackResponse.from_dict -> store_result_once ->
    OAuthCallbackResult) drops it, the MCP SDK's `validate_authorization_response_iss`
    rejects an otherwise-successful callback.
    """
    port = find_available_port()
    result = OAuthCallbackResult()
    result_ready = anyio.Event()
    server = create_oauth_callback_server(
        port=port,
        result_container=result,
        result_ready=result_ready,
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)

        await _wait_until_listening(server)

        async with httpx2.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback",
                params={
                    "code": "good",
                    "state": "s1",
                    "iss": "https://issuer.example.com",
                },
            )
            assert response.status_code == 200

        await result_ready.wait()

        assert result.error is None
        assert result.code == "good"
        assert result.state == "s1"
        assert result.iss == "https://issuer.example.com"

        tg.cancel_scope.cancel()


async def test_oauth_callback_result_captures_iss_on_error():
    """RFC 9207 applies to error redirects too -- the server emits `iss` on
    them, so the callback server must not silently drop it while building the
    error result.
    """
    port = find_available_port()
    result = OAuthCallbackResult()
    result_ready = anyio.Event()
    server = create_oauth_callback_server(
        port=port,
        result_container=result,
        result_ready=result_ready,
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)

        await _wait_until_listening(server)

        async with httpx2.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback",
                params={
                    "error": "access_denied",
                    "state": "s1",
                    "iss": "https://issuer.example.com",
                },
            )
            assert response.status_code == 400

        await result_ready.wait()

        assert result.error is not None
        assert result.iss == "https://issuer.example.com"

        tg.cancel_scope.cancel()
