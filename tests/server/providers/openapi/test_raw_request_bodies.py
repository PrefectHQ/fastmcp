"""Non-JSON request bodies retain the media type required by the endpoint."""

import httpx2
from fastapi import Body, FastAPI, Request

from fastmcp import Client, FastMCP


async def test_plain_text_body_reaches_http_endpoint() -> None:
    app = FastAPI()

    @app.post("/echo", operation_id="echo")
    def echo(
        request: Request, body: str = Body(media_type="text/plain")
    ) -> dict[str, str | None]:
        return {"text": body, "content_type": request.headers.get("content-type")}

    async with httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.ASGITransport(app=app)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=app.openapi(), client=http_client)
        async with Client(server) as client:
            result = await client.call_tool("echo", {"body": "hello from a tool"})

    assert result.data == {"text": "hello from a tool", "content_type": "text/plain"}
