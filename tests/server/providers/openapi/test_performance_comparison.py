"""Performance tests for OpenAPIProvider implementation."""

import gc
import time

import httpx
import pytest

from fastmcp import FastMCP
from fastmcp.server.providers.openapi import OpenAPIProvider


def create_openapi_server(
    openapi_spec: dict,
    client,
    name: str = "OpenAPI Server",
) -> FastMCP:
    """Helper to create a FastMCP server with OpenAPIProvider."""
    provider = OpenAPIProvider(openapi_spec=openapi_spec, client=client)
    mcp = FastMCP(name)
    mcp.add_provider(provider)
    return mcp


class TestPerformance:
    """Test performance of OpenAPIProvider implementation."""

    @pytest.fixture
    def comprehensive_spec(self):
        """Comprehensive OpenAPI spec for performance testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Performance Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "list_users",
                        "summary": "List users",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer", "default": 10},
                            },
                            {
                                "name": "offset",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer", "default": 0},
                            },
                        ],
                        "responses": {"200": {"description": "Users listed"}},
                    },
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                            "age": {"type": "integer"},
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "User created"}},
                    },
                },
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get user",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "User found"}},
                    },
                    "put": {
                        "operationId": "update_user",
                        "summary": "Update user",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                            "age": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "User updated"}},
                    },
                    "delete": {
                        "operationId": "delete_user",
                        "summary": "Delete user",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"204": {"description": "User deleted"}},
                    },
                },
                "/search": {
                    "get": {
                        "operationId": "search_users",
                        "summary": "Search users",
                        "parameters": [
                            {
                                "name": "q",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "filters",
                                "in": "query",
                                "required": False,
                                "style": "deepObject",
                                "explode": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "age_min": {"type": "integer"},
                                        "age_max": {"type": "integer"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["active", "inactive"],
                                        },
                                    },
                                },
                            },
                        ],
                        "responses": {"200": {"description": "Search results"}},
                    }
                },
            },
        }

    def test_provider_initialization_performance(self, comprehensive_spec):
        """Test that provider initialization is fast (serverless requirement)."""
        num_iterations = 5

        # Measure provider initialization
        times = []
        for _ in range(num_iterations):
            client = httpx.AsyncClient(base_url="https://api.example.com")
            start_time = time.time()
            provider = OpenAPIProvider(
                openapi_spec=comprehensive_spec,
                client=client,
            )
            # Ensure provider is fully initialized
            assert provider is not None
            end_time = time.time()
            times.append(end_time - start_time)

        avg_time = sum(times) / len(times)
        max_acceptable_time = 0.1  # 100ms

        print(f"Average initialization time: {avg_time:.4f}s")
        print(f"Performance: {'✓' if avg_time < max_acceptable_time else '✗'}")

        # Should initialize in under 100ms for serverless requirements
        assert avg_time < max_acceptable_time, (
            f"Provider should initialize in under 100ms, got {avg_time:.4f}s"
        )

    def test_server_initialization_performance(self, comprehensive_spec):
        """Test that full server initialization is fast."""
        num_iterations = 5

        times = []
        for _ in range(num_iterations):
            client = httpx.AsyncClient(base_url="https://api.example.com")
            start_time = time.time()
            server = create_openapi_server(
                openapi_spec=comprehensive_spec,
                client=client,
                name="Performance Test",
            )
            # Ensure server is fully initialized
            assert server is not None
            end_time = time.time()
            times.append(end_time - start_time)

        avg_time = sum(times) / len(times)
        max_acceptable_time = 0.1  # 100ms

        print(f"Average server initialization time: {avg_time:.4f}s")

        assert avg_time < max_acceptable_time, (
            f"Server should initialize in under 100ms, got {avg_time:.4f}s"
        )

    async def test_functionality_after_optimization(self, comprehensive_spec):
        """Verify that performance optimization doesn't break functionality."""
        client = httpx.AsyncClient(base_url="https://api.example.com")

        server = create_openapi_server(
            openapi_spec=comprehensive_spec,
            client=client,
            name="Test Server",
        )

        # Get tools from the server via public API
        tools = await server.list_tools()

        # Should have 6 operations in the spec
        assert len(tools) == 6

        # Expected operations
        expected_operations = {
            "list_users",
            "create_user",
            "get_user",
            "update_user",
            "delete_user",
            "search_users",
        }
        assert {t.name for t in tools} == expected_operations

    def test_memory_efficiency(self, comprehensive_spec):
        """Test that implementation doesn't significantly increase memory usage."""

        # Helper to count total tools across all providers
        def count_provider_tools(server):
            total = 0
            for provider in server.providers:
                if hasattr(provider, "_tools"):
                    total += len(provider._tools)
            return total

        gc.collect()  # Clean up before baseline
        baseline_refs = len(gc.get_objects())

        servers = []
        for i in range(10):
            client = httpx.AsyncClient(base_url="https://api.example.com")
            server = create_openapi_server(
                openapi_spec=comprehensive_spec,
                client=client,
                name=f"Memory Test Server {i}",
            )
            servers.append(server)

        # Servers should all be functional
        assert len(servers) == 10
        assert all(count_provider_tools(s) == 6 for s in servers)

        # Memory usage shouldn't explode
        gc.collect()
        current_refs = len(gc.get_objects())
        growth_ratio = current_refs / max(baseline_refs, 1)
        assert growth_ratio < 3.0, (
            f"Memory usage grew by {growth_ratio}x, which seems excessive"
        )
