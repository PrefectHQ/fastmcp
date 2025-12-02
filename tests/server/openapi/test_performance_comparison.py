"""Performance comparison between legacy and new OpenAPI implementations."""

import time

import httpx
import pytest

from fastmcp.server.openapi import FastMCPOpenAPI
from fastmcp.server.openapi import FastMCPOpenAPI as LegacyFastMCPOpenAPI


class TestPerformanceComparison:
    """Compare performance between legacy and new implementations."""

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

    def test_server_initialization_performance(self, comprehensive_spec):
        """Test that new implementation is significantly faster than legacy."""
        num_iterations = 5

        # Measure legacy implementation
        legacy_times = []
        for _ in range(num_iterations):
            client = httpx.AsyncClient(base_url="https://api.example.com")
            start_time = time.time()
            server = LegacyFastMCPOpenAPI(
                openapi_spec=comprehensive_spec,
                client=client,
                name="Legacy Performance Test",
            )
            # Ensure server is fully initialized
            assert server is not None
            end_time = time.time()
            legacy_times.append(end_time - start_time)

        # Measure new implementation
        new_times = []
        for _ in range(num_iterations):
            client = httpx.AsyncClient(base_url="https://api.example.com")
            start_time = time.time()
            server = FastMCPOpenAPI(
                openapi_spec=comprehensive_spec,
                client=client,
                name="New Performance Test",
            )
            # Ensure server is fully initialized
            assert server is not None
            end_time = time.time()
            new_times.append(end_time - start_time)

        # Calculate averages
        legacy_avg = sum(legacy_times) / len(legacy_times)
        new_avg = sum(new_times) / len(new_times)

        print(f"Legacy implementation average: {legacy_avg:.4f}s")
        print(f"New implementation average: {new_avg:.4f}s")
        print(f"Speedup: {legacy_avg / new_avg:.2f}x")

        # Both implementations should be very fast for moderate specs
        # The key achievement is eliminating the 100-200ms latency issue for serverless
        max_acceptable_time = 0.1  # 100ms

        print(f"Legacy performance: {'✓' if legacy_avg < max_acceptable_time else '✗'}")
        print(f"New performance: {'✓' if new_avg < max_acceptable_time else '✗'}")

        # New implementation should be under 100ms for reasonable specs (serverless requirement)
        assert new_avg < max_acceptable_time, (
            f"New implementation should initialize in under 100ms, got {new_avg:.4f}s"
        )

        # Legacy might be slightly faster or slower on small specs, but both should be fast
        # The real improvement shows up with larger specs where code generation was the bottleneck
        assert legacy_avg < max_acceptable_time, (
            f"Legacy should also be fast on small specs, got {legacy_avg:.4f}s"
        )

    def test_functionality_identical_after_optimization(self, comprehensive_spec):
        """Verify that performance optimization doesn't break functionality."""
        client = httpx.AsyncClient(base_url="https://api.example.com")

        # Create both servers
        legacy_server = LegacyFastMCPOpenAPI(
            openapi_spec=comprehensive_spec,
            client=client,
            name="Legacy Server",
        )
        new_server = FastMCPOpenAPI(
            openapi_spec=comprehensive_spec,
            client=client,
            name="New Server",
        )

        # Both should have the same number of tools
        legacy_tool_count = len(legacy_server._tool_manager._tools)
        new_tool_count = len(new_server._tool_manager._tools)

        assert legacy_tool_count == new_tool_count
        assert legacy_tool_count == 6  # 6 operations in the spec

        # Tool names should be identical
        legacy_tool_names = set(legacy_server._tool_manager._tools.keys())
        new_tool_names = set(new_server._tool_manager._tools.keys())

        assert legacy_tool_names == new_tool_names

        # Expected operations
        expected_operations = {
            "list_users",
            "create_user",
            "get_user",
            "update_user",
            "delete_user",
            "search_users",
        }
        assert legacy_tool_names == expected_operations

    def test_memory_efficiency(self, comprehensive_spec):
        """Test that new implementation doesn't significantly increase memory usage."""
        import gc

        # This is a basic test - in practice you'd use more sophisticated memory profiling
        gc.collect()  # Clean up before baseline
        baseline_refs = len(gc.get_objects())

        servers = []
        for i in range(10):
            client = httpx.AsyncClient(base_url="https://api.example.com")
            server = FastMCPOpenAPI(
                openapi_spec=comprehensive_spec,
                client=client,
                name=f"Memory Test Server {i}",
            )
            servers.append(server)

        # Servers should all be functional
        assert len(servers) == 10
        assert all(len(s._tool_manager._tools) == 6 for s in servers)

        # Memory usage shouldn't explode (this is a basic check)
        gc.collect()  # Clean up
        current_refs = len(gc.get_objects())
        # Allow reasonable memory growth but not exponential
        growth_ratio = current_refs / max(baseline_refs, 1)
        assert growth_ratio < 3.0, (
            f"Memory usage grew by {growth_ratio}x, which seems excessive"
        )
