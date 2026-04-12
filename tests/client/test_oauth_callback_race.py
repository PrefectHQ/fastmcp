import anyio
import httpx

from fastmcp.client.oauth_callback import (
    OAuthCallbackResult,
    create_oauth_callback_server,
)
from fastmcp.utilities.http import find_available_port


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

        await anyio.sleep(0.05)

        async with httpx.AsyncClient() as client:
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
