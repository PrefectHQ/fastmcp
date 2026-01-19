"""Performance regression tests for OpenAPI parsing.

These tests ensure that large OpenAPI schemas (like GitHub's API) parse quickly
and don't regress to the slow performance we had before optimization.
"""

import time

import httpx
import pytest

from fastmcp import FastMCP


class TestOpenAPIPerformance:
    """Performance tests for OpenAPI parsing with real-world large schemas."""

    # 10 second maximum timeout for this test no matter what
    @pytest.mark.integration
    @pytest.mark.timeout(10)
    async def test_github_api_schema_performance(self):
        """
        Test that GitHub's full API schema parses quickly.

        This is a regression test to ensure our performance optimizations
        (eliminating deepcopy, single-pass optimization, smart union adjustment)
        continue to work. Without these optimizations, this test would take
        multiple minutes to parse.

        On a local machine, this tests passes in ~2 seconds, but in GHA CI we see
        times as high as 6-7 seconds, so the test is asserted to pass in under
        10. Given that, this isn't intended to be a strict performance test, but
        rather a canary to ensure we don't regress significantly.
        """

        # Download the full GitHub API schema (typically ~10MB)
        response = httpx.get(
            "https://raw.githubusercontent.com/github/rest-api-description/refs/heads/main/descriptions-next/ghes-3.17/ghes-3.17.json",
            timeout=30.0,  # Allow time for download
        )
        response.raise_for_status()
        schema = response.json()

        # Time the parsing operation
        start_time = time.time()

        # This should complete quickly with our optimizations
        mcp_server = FastMCP.from_openapi(schema, httpx.AsyncClient())

        elapsed_time = time.time() - start_time

        print(f"OpenAPI parsing took {elapsed_time:.2f}s")

        # Verify the server was created successfully
        assert mcp_server is not None

        # Performance regression test: should complete in under 10 seconds
        assert elapsed_time < 10.0, (
            f"OpenAPI parsing took {elapsed_time:.2f}s, exceeding 10s limit. "
            f"This suggests a performance regression."
        )

        # Verify server and tools were created successfully
        tools = await mcp_server.list_tools()
        assert len(tools) > 500

    def test_medium_schema_performance(self):
        """
        Test parsing performance with a smaller synthetic schema.

        This test doesn't require network access and provides a baseline
        for performance testing in CI environments.
        """
        # Create a medium-sized synthetic schema
        schema = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {},
        }

        # Generate multiple paths to create a reasonably sized schema
        for i in range(100):
            path = f"/test/{i}"
            schema["paths"][path] = {  # type: ignore[index]
                "get": {
                    "operationId": f"test_{i}",
                    "parameters": [
                        {"name": "param1", "in": "query", "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                            "data": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "properties": {
                                                    "value": {"type": "string"},
                                                    "metadata": {
                                                        "type": "object",
                                                        "properties": {
                                                            "created": {
                                                                "type": "string"
                                                            },
                                                            "updated": {
                                                                "type": "string"
                                                            },
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }

        # Time the parsing
        start_time = time.time()
        mcp_server = FastMCP.from_openapi(schema, httpx.AsyncClient())
        elapsed_time = time.time() - start_time

        # Should be very fast for medium schemas (well under 1 second)
        assert elapsed_time < 1.0, (
            f"Medium schema parsing took {elapsed_time:.3f}s, expected <1s"
        )
        assert mcp_server is not None
