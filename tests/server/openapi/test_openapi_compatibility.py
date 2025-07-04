import json

import httpx
import pytest
from pydantic.networks import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.openapi import FastMCPOpenAPI

from .conftest import GET_ROUTE_MAPS


class TestOpenAPI30Compatibility:
    """Tests for compatibility with OpenAPI 3.0 specifications."""

    @pytest.fixture
    def openapi_30_spec(self) -> dict:
        """Fixture that returns a simple OpenAPI 3.0 specification."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Product API (3.0)", "version": "1.0.0"},
            "paths": {
                "/products": {
                    "get": {
                        "operationId": "listProducts",
                        "summary": "List all products",
                        "responses": {"200": {"description": "A list of products"}},
                    },
                    "post": {
                        "operationId": "createProduct",
                        "summary": "Create a new product",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "price": {"type": "number"},
                                        },
                                        "required": ["name", "price"],
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "Product created"}},
                    },
                },
                "/products/{product_id}": {
                    "get": {
                        "operationId": "getProduct",
                        "summary": "Get product by ID",
                        "parameters": [
                            {
                                "name": "product_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "A product"}},
                    }
                },
            },
        }

    @pytest.fixture
    async def mock_30_client(self) -> httpx.AsyncClient:
        """Mock client that returns predefined responses for the 3.0 API."""

        async def _responder(request):
            if request.url.path == "/products" and request.method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {"id": "p1", "name": "Product 1", "price": 19.99},
                        {"id": "p2", "name": "Product 2", "price": 29.99},
                    ],
                )
            elif request.url.path == "/products" and request.method == "POST":
                import json

                data = json.loads(request.content)
                return httpx.Response(
                    201, json={"id": "p3", "name": data["name"], "price": data["price"]}
                )
            elif request.url.path.startswith("/products/") and request.method == "GET":
                product_id = request.url.path.split("/")[-1]
                products = {
                    "p1": {"id": "p1", "name": "Product 1", "price": 19.99},
                    "p2": {"id": "p2", "name": "Product 2", "price": 29.99},
                }
                if product_id in products:
                    return httpx.Response(200, json=products[product_id])
                return httpx.Response(404, json={"error": "Product not found"})
            return httpx.Response(404)

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    async def openapi_30_server_with_all_types(
        self, openapi_30_spec, mock_30_client
    ) -> FastMCPOpenAPI:
        """Create a FastMCPOpenAPI server from the OpenAPI 3.0 spec."""
        return FastMCPOpenAPI(
            openapi_spec=openapi_30_spec,
            client=mock_30_client,
            name="Product API 3.0",
            route_maps=GET_ROUTE_MAPS,
        )

    async def test_server_creation(self, openapi_30_server_with_all_types):
        """Test that a server can be created from an OpenAPI 3.0 spec."""
        assert isinstance(openapi_30_server_with_all_types, FastMCP)
        assert openapi_30_server_with_all_types.name == "Product API 3.0"

    async def test_resource_discovery(self, openapi_30_server_with_all_types):
        """Test that resources are correctly discovered from an OpenAPI 3.0 spec."""
        async with Client(openapi_30_server_with_all_types) as client:
            resources = await client.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == AnyUrl("resource://listProducts")

    async def test_resource_template_discovery(self, openapi_30_server_with_all_types):
        """Test that resource templates are correctly discovered from an OpenAPI 3.0 spec."""
        async with Client(openapi_30_server_with_all_types) as client:
            templates = await client.list_resource_templates()
        assert len(templates) == 1
        assert templates[0].name == "getProduct"
        assert templates[0].uriTemplate == r"resource://getProduct/{product_id}"

    async def test_tool_discovery(self, openapi_30_server_with_all_types):
        """Test that tools are correctly discovered from an OpenAPI 3.0 spec."""
        async with Client(openapi_30_server_with_all_types) as client:
            tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "createProduct"
        assert "name" in tools[0].inputSchema["properties"]
        assert "price" in tools[0].inputSchema["properties"]

    async def test_resource_access(self, openapi_30_server_with_all_types):
        """Test reading a resource from an OpenAPI 3.0 server."""
        async with Client(openapi_30_server_with_all_types) as client:
            resource_response = await client.read_resource("resource://listProducts")
            response_text = resource_response[0].text  # type: ignore[attr-defined]
            content = json.loads(response_text)
        assert len(content) == 2
        assert content[0]["name"] == "Product 1"
        assert content[1]["name"] == "Product 2"

    async def test_resource_template_access(self, openapi_30_server_with_all_types):
        """Test reading a resource from template from an OpenAPI 3.0 server."""
        async with Client(openapi_30_server_with_all_types) as client:
            resource_response = await client.read_resource("resource://getProduct/p1")
            response_text = resource_response[0].text  # type: ignore[attr-defined]
            content = json.loads(response_text)
        assert content["id"] == "p1"
        assert content["name"] == "Product 1"
        assert content["price"] == 19.99

    async def test_tool_execution(self, openapi_30_server_with_all_types):
        """Test executing a tool from an OpenAPI 3.0 server."""
        async with Client(openapi_30_server_with_all_types) as client:
            result = await client.call_tool(
                "createProduct", {"name": "New Product", "price": 39.99}
            )
            # Result should be a text content
            assert len(result.content) == 1
            product = json.loads(result.content[0].text)  # type: ignore[attr-defined]
            assert product["id"] == "p3"
            assert product["name"] == "New Product"
            assert product["price"] == 39.99

            assert result.structured_content is not None
            assert result.structured_content["id"] == "p3"
            assert result.structured_content["name"] == "New Product"
            assert result.structured_content["price"] == 39.99

            assert result.data is not None
            assert result.data["id"] == "p3"
            assert result.data["name"] == "New Product"
            assert result.data["price"] == 39.99


class TestOpenAPI31Compatibility:
    """Tests for compatibility with OpenAPI 3.1 specifications."""

    @pytest.fixture
    def openapi_31_spec(self) -> dict:
        """Fixture that returns a simple OpenAPI 3.1 specification."""
        return {
            "openapi": "3.1.0",
            "info": {"title": "Order API (3.1)", "version": "1.0.0"},
            "paths": {
                "/orders": {
                    "get": {
                        "operationId": "listOrders",
                        "summary": "List all orders",
                        "responses": {"200": {"description": "A list of orders"}},
                    },
                    "post": {
                        "operationId": "createOrder",
                        "summary": "Place a new order",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "customer": {"type": "string"},
                                            "items": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        },
                                        "required": ["customer", "items"],
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "Order created"}},
                    },
                },
                "/orders/{order_id}": {
                    "get": {
                        "operationId": "getOrder",
                        "summary": "Get order by ID",
                        "parameters": [
                            {
                                "name": "order_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "An order"}},
                    }
                },
            },
        }

    @pytest.fixture
    async def mock_31_client(self) -> httpx.AsyncClient:
        """Mock client that returns predefined responses for the 3.1 API."""

        async def _responder(request):
            if request.url.path == "/orders" and request.method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {"id": "o1", "customer": "Alice", "items": ["item1", "item2"]},
                        {"id": "o2", "customer": "Bob", "items": ["item3"]},
                    ],
                )
            elif request.url.path == "/orders" and request.method == "POST":
                import json

                data = json.loads(request.content)
                return httpx.Response(
                    201,
                    json={
                        "id": "o3",
                        "customer": data["customer"],
                        "items": data["items"],
                    },
                )
            elif request.url.path.startswith("/orders/") and request.method == "GET":
                order_id = request.url.path.split("/")[-1]
                orders = {
                    "o1": {
                        "id": "o1",
                        "customer": "Alice",
                        "items": ["item1", "item2"],
                    },
                    "o2": {"id": "o2", "customer": "Bob", "items": ["item3"]},
                }
                if order_id in orders:
                    return httpx.Response(200, json=orders[order_id])
                return httpx.Response(404, json={"error": "Order not found"})
            return httpx.Response(404)

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    async def openapi_31_server_with_all_types(
        self, openapi_31_spec, mock_31_client
    ) -> FastMCPOpenAPI:
        """Create a FastMCPOpenAPI server from the OpenAPI 3.1 spec."""
        return FastMCPOpenAPI(
            openapi_spec=openapi_31_spec,
            client=mock_31_client,
            name="Order API 3.1",
            route_maps=GET_ROUTE_MAPS,
        )

    async def test_server_creation(self, openapi_31_server_with_all_types):
        """Test that a server can be created from an OpenAPI 3.1 spec."""
        assert isinstance(openapi_31_server_with_all_types, FastMCP)
        assert openapi_31_server_with_all_types.name == "Order API 3.1"

    async def test_resource_discovery(self, openapi_31_server_with_all_types):
        """Test that resources are correctly discovered from an OpenAPI 3.1 spec."""
        async with Client(openapi_31_server_with_all_types) as client:
            resources = await client.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == AnyUrl("resource://listOrders")

    async def test_resource_template_discovery(self, openapi_31_server_with_all_types):
        """Test that resource templates are correctly discovered from an OpenAPI 3.1 spec."""
        async with Client(openapi_31_server_with_all_types) as client:
            templates = await client.list_resource_templates()
        assert len(templates) == 1
        assert templates[0].name == "getOrder"
        assert templates[0].uriTemplate == r"resource://getOrder/{order_id}"

    async def test_tool_discovery(self, openapi_31_server_with_all_types):
        """Test that tools are correctly discovered from an OpenAPI 3.1 spec."""
        async with Client(openapi_31_server_with_all_types) as client:
            tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "createOrder"
        assert "customer" in tools[0].inputSchema["properties"]
        assert "items" in tools[0].inputSchema["properties"]

    async def test_resource_access(self, openapi_31_server_with_all_types):
        """Test reading a resource from an OpenAPI 3.1 server."""
        async with Client(openapi_31_server_with_all_types) as client:
            resource_response = await client.read_resource("resource://listOrders")
            response_text = resource_response[0].text  # type: ignore[attr-defined]
            content = json.loads(response_text)
        assert len(content) == 2
        assert content[0]["customer"] == "Alice"
        assert content[1]["customer"] == "Bob"

    async def test_resource_template_access(self, openapi_31_server_with_all_types):
        """Test reading a resource from template from an OpenAPI 3.1 server."""
        async with Client(openapi_31_server_with_all_types) as client:
            resource_response = await client.read_resource("resource://getOrder/o1")
            response_text = resource_response[0].text  # type: ignore[attr-defined]
            content = json.loads(response_text)
        assert content["id"] == "o1"
        assert content["customer"] == "Alice"
        assert content["items"] == ["item1", "item2"]

    async def test_tool_execution(self, openapi_31_server_with_all_types):
        """Test executing a tool from an OpenAPI 3.1 server."""
        async with Client(openapi_31_server_with_all_types) as client:
            result = await client.call_tool(
                "createOrder", {"customer": "Charlie", "items": ["item4", "item5"]}
            )
            # Result should be a text content
            assert len(result.content) == 1
            order = json.loads(result.content[0].text)  # type: ignore[attr-defined]
            assert order["id"] == "o3"
            assert order["customer"] == "Charlie"
            assert order["items"] == ["item4", "item5"]

            assert result.structured_content is not None
            assert result.structured_content["id"] == "o3"
            assert result.structured_content["customer"] == "Charlie"
            assert result.structured_content["items"] == ["item4", "item5"]

            assert result.data is not None
            assert result.data["id"] == "o3"
            assert result.data["customer"] == "Charlie"
            assert result.data["items"] == ["item4", "item5"]
