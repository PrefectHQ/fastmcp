import pytest

from fastmcp.server.http import StreamableHTTPASGIApp


async def test_uninitialized_task_group_error_points_to_lifespan_docs():
    app = StreamableHTTPASGIApp(session_manager=None)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    with pytest.raises(RuntimeError) as exc_info:
        await app({"type": "http"}, receive, send)

    message = str(exc_info.value)
    assert "\\n" not in message
    assert "`fastmcp_instance.http_app()`.\nFor more details" in message
    assert (
        "https://gofastmcp.com/deployment/http#integration-with-web-frameworks"
        in message
    )
    assert message.endswith(
        "\nOriginal error: Task group is not initialized. Make sure to use run()."
    )
