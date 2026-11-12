"""Unit tests for OpenAPIProvider."""

import asyncio
from typing import Any

import httpx2
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.server.providers.openapi.provider import DEFAULT_TIMEOUT
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap


class TestOpenAPIProviderServerVariables:
    """Test that OpenAPIProvider resolves OpenAPI 3.x server variables."""

    def test_server_variables_substituted_with_defaults(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [
                {
                    "url": "https://{region}.api.example.com/v1",
                    "variables": {
                        "region": {
                            "default": "us",
                            "enum": ["us", "eu", "apac"],
                        }
                    },
                }
            ],
            "paths": {},
        }
        client = OpenAPIProvider._create_default_client(spec)
        assert str(client.base_url) == "https://us.api.example.com/v1/"

    def test_multiple_server_variables_substituted(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [
                {
                    "url": "{scheme}://{host}/v1",
                    "variables": {
                        "scheme": {"default": "https"},
                        "host": {"default": "api.example.com"},
                    },
                }
            ],
            "paths": {},
        }
        client = OpenAPIProvider._create_default_client(spec)
        assert str(client.base_url) == "https://api.example.com/v1/"

    def test_static_server_url_unaffected(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {},
        }
        client = OpenAPIProvider._create_default_client(spec)
        assert str(client.base_url) == "https://api.example.com"


class TestOpenAPIProviderBasicFunctionality:
    """Test basic OpenAPIProvider functionality."""

    @pytest.fixture
    def simple_openapi_spec(self):
        """Simple OpenAPI spec for testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user",
                        "summary": "Get user by ID",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "User retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/users": {
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create a new user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "User created successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                                "email": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

    def test_provider_initialization(self, simple_openapi_spec):
        """Test provider initialization with OpenAPI spec."""
        client = httpx2.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=client)

        # Should have initialized RequestDirector successfully
        assert hasattr(provider, "_director")
        assert hasattr(provider, "_spec")

    def test_server_with_provider(self, simple_openapi_spec):
        """Test server initialization with OpenAPIProvider."""
        client = httpx2.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=client)

        mcp = FastMCP("Test Server")
        mcp.add_provider(provider)

        assert mcp.name == "Test Server"

    async def test_provider_creates_tools_from_spec(self, simple_openapi_spec):
        """Test that provider creates tools from OpenAPI spec."""
        async with httpx2.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=client)

            mcp = FastMCP("Test Server")
            mcp.add_provider(provider)

            async with Client(mcp) as mcp_client:
                tools = await mcp_client.list_tools()

                # Should have created tools for both operations
                assert len(tools) == 2

                tool_names = {tool.name for tool in tools}
                assert "get_user" in tool_names
                assert "create_user" in tool_names

    async def test_provider_tool_execution(self, simple_openapi_spec):
        """Test tool execution uses RequestDirector."""
        mock_client = httpx2.AsyncClient()
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec, client=mock_client)

        mcp = FastMCP("Test Server")
        mcp.add_provider(provider)

        async with Client(mcp) as mcp_client:
            tools = await mcp_client.list_tools()

            # Should have tools using RequestDirector
            assert len(tools) == 2

            get_user_tool = next(tool for tool in tools if tool.name == "get_user")
            assert get_user_tool is not None
            assert get_user_tool.description is not None

    def test_provider_creates_default_client_from_spec(self, simple_openapi_spec):
        """Test that omitting client creates one from the spec's servers URL."""
        provider = OpenAPIProvider(openapi_spec=simple_openapi_spec)
        assert str(provider._client.base_url).rstrip("/") == "https://api.example.com"
        assert provider._client.timeout == httpx2.Timeout(DEFAULT_TIMEOUT)

    def test_provider_default_client_requires_servers(self):
        """Test that omitting client without servers in spec raises."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "No Servers", "version": "1.0.0"},
            "paths": {},
        }
        with pytest.raises(ValueError, match="No server URL"):
            OpenAPIProvider(openapi_spec=spec)

    def test_provider_with_empty_spec(self):
        """Test provider with minimal OpenAPI spec."""
        minimal_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Empty API", "version": "1.0.0"},
            "paths": {},
        }

        client = httpx2.AsyncClient(base_url="https://api.example.com")
        provider = OpenAPIProvider(openapi_spec=minimal_spec, client=client)

        # Should handle empty paths gracefully
        assert hasattr(provider, "_director")
        assert hasattr(provider, "_spec")

    async def test_clean_schema_output_no_unused_defs(self):
        """Test that unused schema definitions are removed from tool schemas."""
        spec_with_unused_defs = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "create_user",
                        "summary": "Create a new user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string", "title": "Name"},
                                            "active": {
                                                "type": "boolean",
                                                "title": "Active",
                                            },
                                        },
                                        "required": ["name", "active"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "User created successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {
                                                    "type": "integer",
                                                    "title": "Id",
                                                },
                                                "name": {
                                                    "type": "string",
                                                    "title": "Name",
                                                },
                                                "active": {
                                                    "type": "boolean",
                                                    "title": "Active",
                                                },
                                            },
                                            "required": ["id", "name", "active"],
                                            "title": "User",
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "HTTPValidationError": {
                        "properties": {
                            "detail": {
                                "items": {
                                    "$ref": "#/components/schemas/ValidationError"
                                },
                                "title": "Detail",
                                "type": "array",
                            }
                        },
                        "title": "HTTPValidationError",
                        "type": "object",
                    },
                    "ValidationError": {
                        "properties": {
                            "loc": {
                                "items": {
                                    "anyOf": [{"type": "string"}, {"type": "integer"}]
                                },
                                "title": "Location",
                                "type": "array",
                            },
                            "msg": {"title": "Message", "type": "string"},
                            "type": {"title": "Error Type", "type": "string"},
                        },
                        "required": ["loc", "msg", "type"],
                        "title": "ValidationError",
                        "type": "object",
                    },
                }
            },
        }

        async with httpx2.AsyncClient(base_url="https://api.example.com") as client:
            provider = OpenAPIProvider(
                openapi_spec=spec_with_unused_defs, client=client
            )
            mcp = FastMCP("Test Server")
            mcp.add_provider(provider)

            async with Client(mcp) as mcp_client:
                tools = await mcp_client.list_tools()

                assert len(tools) == 1
                tool = tools[0]

                assert tool.name == "create_user"

                expected_input_schema = {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "title": "Name"},
                        "active": {"type": "boolean", "title": "Active"},
                    },
                    "required": ["name", "active"],
                }
                assert tool.input_schema == expected_input_schema

                expected_output_schema = {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "title": "Id"},
                        "name": {"type": "string", "title": "Name"},
                        "active": {"type": "boolean", "title": "Active"},
                    },
                    "required": ["id", "name", "active"],
                    "title": "User",
                }
                assert tool.output_schema == expected_output_schema


def names_spec(*routes: tuple[str, str]) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Names", "version": "1.0.0"},
        "paths": {
            path: {
                "get": {
                    "operationId": operation_id,
                    "responses": {"200": {"description": "OK"}},
                }
            }
            for path, operation_id in routes
        },
    }


class TestOpenAPIProviderComponentNames:
    """Generated component names stay unique so no route is silently dropped."""

    async def test_duplicate_names_get_numbered_suffixes(self):
        spec = names_spec(
            ("/a", "list_items__a"), ("/b", "list_items__b"), ("/c", "list_items__c")
        )
        server = FastMCP.from_openapi(
            spec, client=httpx2.AsyncClient(base_url="http://test")
        )

        async with Client(server) as client:
            tools = await client.list_tools()

        assert sorted(tool.name for tool in tools) == [
            "list_items",
            "list_items_2",
            "list_items_3",
        ]

    @pytest.mark.parametrize(
        "routes",
        [
            [("/a", "list_items__a"), ("/b", "list_items__b"), ("/c", "list_items_2")],
            [("/c", "list_items_2"), ("/a", "list_items__a"), ("/b", "list_items__b")],
        ],
    )
    async def test_suffix_does_not_reuse_an_existing_name(
        self, routes: list[tuple[str, str]]
    ):
        requested_paths: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requested_paths.append(request.url.path)
            return httpx2.Response(200, json={})

        api = httpx2.AsyncClient(
            base_url="http://test", transport=httpx2.MockTransport(handler)
        )
        server = FastMCP.from_openapi(names_spec(*routes), client=api)

        async with Client(server) as client:
            tools = await client.list_tools()
            for tool in tools:
                await client.call_tool(tool.name, {})

        assert len(tools) == 3
        assert len({tool.name for tool in tools}) == 3
        assert sorted(requested_paths) == ["/a", "/b", "/c"]

    async def test_resource_suffix_does_not_reuse_an_existing_name(self):
        spec = names_spec(
            ("/a", "list_items__a"), ("/b", "list_items__b"), ("/c", "list_items_2")
        )
        server = FastMCP.from_openapi(
            spec,
            client=httpx2.AsyncClient(base_url="http://test"),
            route_maps=[RouteMap(mcp_type=MCPType.RESOURCE)],
        )

        async with Client(server) as client:
            resources = await client.list_resources()

        assert len({str(resource.uri) for resource in resources}) == 3


class TestOpenAPIProviderLifespanReuse:
    """Test OpenAPIProvider client lifecycle across multiple or concurrent lifespans."""

    @staticmethod
    def _spec() -> dict[str, Any]:
        return {
            "openapi": "3.0.0",
            "info": {"title": "Tiny", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "list_items",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/items/{id}": {
                    "get": {
                        "operationId": "get_item",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

    async def test_from_openapi_without_explicit_client_reconnects(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """FastMCP.from_openapi without client= can be connected multiple times."""
        call_count = 0

        def make_client(
            cls: type[OpenAPIProvider], spec: dict[str, Any]
        ) -> httpx2.AsyncClient:
            def handler(request: httpx2.Request) -> httpx2.Response:
                nonlocal call_count
                call_count += 1
                return httpx2.Response(200, json={"attempt": call_count})

            return httpx2.AsyncClient(
                base_url="https://api.example.com",
                transport=httpx2.MockTransport(handler),
            )

        monkeypatch.setattr(
            OpenAPIProvider, "_create_default_client", classmethod(make_client)
        )

        mcp = FastMCP.from_openapi(openapi_spec=self._spec(), name="Tiny")
        for attempt in (1, 2):
            async with Client(mcp) as client:
                tools = await client.list_tools()
                assert len(tools) >= 1
                result = await client.call_tool("list_items", {})
                assert result.data == {"attempt": attempt}

    async def test_concurrent_lifespans_share_and_close_client_safely(self):
        """Concurrent lifespans on one OpenAPIProvider must not double-open or close early."""
        provider = OpenAPIProvider(openapi_spec=self._spec())
        first_client = provider._client

        async with provider.lifespan():
            assert not first_client.is_closed
            async with provider.lifespan():
                assert provider._client is first_client
                assert not first_client.is_closed
            # Inner lifespan exited, outer is still active
            assert not first_client.is_closed

        # Outer lifespan exited, owned client should now be closed
        assert first_client.is_closed

        # Entering another lifespan afterward recreates a fresh open client
        async with provider.lifespan():
            assert provider._client is not first_client
            assert not provider._client.is_closed
            for tool in provider._tools.values():
                assert tool._client is provider._client
            for resource in provider._resources.values():
                assert resource._client is provider._client
            for template in provider._templates.values():
                assert template._client is provider._client
        assert provider._client.is_closed

    async def test_explicit_client_not_closed_or_replaced(self):
        """Explicitly provided clients are not owned, closed, or replaced."""
        explicit = httpx2.AsyncClient(base_url="https://api.example.com")
        try:
            mcp = FastMCP.from_openapi(
                openapi_spec=self._spec(), client=explicit, name="Tiny"
            )
            for _ in (1, 2):
                async with Client(mcp) as client:
                    tools = await client.list_tools()
                    assert len(tools) >= 1
                assert not explicit.is_closed
        finally:
            await explicit.aclose()

    async def test_concurrent_tasks_interleaved_lifespans(self):
        """Separate async tasks entering/exiting OpenAPIProvider lifespan interleaved."""
        provider = OpenAPIProvider(
            openapi_spec=self._spec(),
            route_maps=[
                RouteMap(pattern=r"^/items$", mcp_type=MCPType.RESOURCE),
                RouteMap(
                    pattern=r"^/items/\{id\}$", mcp_type=MCPType.RESOURCE_TEMPLATE
                ),
            ],
        )
        assert len(provider._resources) >= 1
        assert len(provider._templates) >= 1

        first_client = provider._client
        task1_entered = asyncio.Event()
        task2_entered = asyncio.Event()
        task1_can_exit = asyncio.Event()
        task2_can_exit = asyncio.Event()

        async def worker1():
            async with provider.lifespan():
                task1_entered.set()
                await task1_can_exit.wait()

        async def worker2():
            await task1_entered.wait()
            async with provider.lifespan():
                task2_entered.set()
                await task2_can_exit.wait()

        t1 = asyncio.create_task(worker1())
        t2 = asyncio.create_task(worker2())

        await task1_entered.wait()
        await task2_entered.wait()
        assert not first_client.is_closed

        # Task 1 exits first while Task 2 is still running
        task1_can_exit.set()
        await t1
        assert not first_client.is_closed

        # Task 2 exits; now client should close
        task2_can_exit.set()
        await t2
        assert first_client.is_closed

        # Verify a new lifespan updates resources and templates as well
        async with provider.lifespan():
            assert provider._client is not first_client
            for r in provider._resources.values():
                assert r._client is provider._client
            for t in provider._templates.values():
                assert t._client is provider._client
