"""Tests for OpenAPI endpoints returning non-JSON responses."""

import httpx2
import mcp_types
import pytest
from fastapi import FastAPI, Response

from fastmcp import Client, FastMCP


@pytest.mark.asyncio
async def test_openapi_non_json_responses():
    """Verify OpenAPI tools handle binary, text, and JSON responses cleanly (fixes #5367)."""
    app = FastAPI()

    @app.get("/archive")
    async def archive():
        return Response(b"PK\x03\x04\xff\xfe", media_type="application/zip")

    @app.get("/readme")
    async def readme():
        return Response("# Title\n", media_type="text/markdown")

    @app.get("/data")
    async def data():
        return {"status": "ok"}

    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Files API", "version": "1.0.0"},
        "paths": {
            "/archive": {
                "get": {
                    "operationId": "get_archive",
                    "responses": {
                        "200": {
                            "description": "Binary zip archive",
                            "content": {
                                "application/zip": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        }
                    },
                }
            },
            "/readme": {
                "get": {
                    "operationId": "get_readme",
                    "responses": {
                        "200": {
                            "description": "Markdown documentation",
                            "content": {
                                "text/markdown": {"schema": {"type": "string"}}
                            },
                        }
                    },
                }
            },
            "/data": {
                "get": {
                    "operationId": "get_data",
                    "responses": {
                        "200": {
                            "description": "JSON object",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }

    async with httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.ASGITransport(app=app)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)

        # Output schema checks
        archive_tool = await server.get_tool("get_archive")
        assert archive_tool is not None
        assert archive_tool.output_schema is None

        readme_tool = await server.get_tool("get_readme")
        assert readme_tool is not None
        assert readme_tool.output_schema is None

        data_tool = await server.get_tool("get_data")
        assert data_tool is not None
        assert data_tool.output_schema is not None

        # Invocation checks
        async with Client(server) as client:
            archive_res = await client.call_tool("get_archive", {})
            assert archive_res.is_error is False
            assert len(archive_res.content) == 1
            content_block = archive_res.content[0]
            assert isinstance(content_block, mcp_types.EmbeddedResource)
            assert isinstance(content_block.resource, mcp_types.BlobResourceContents)
            assert content_block.resource.mime_type == "application/zip"

            readme_res = await client.call_tool("get_readme", {})
            assert readme_res.is_error is False
            assert len(readme_res.content) == 1
            readme_block = readme_res.content[0]
            assert isinstance(readme_block, mcp_types.TextContent)
            assert readme_block.text == "# Title\n"

            data_res = await client.call_tool("get_data", {})
            assert data_res.is_error is False
            assert data_res.structured_content == {"status": "ok"}
