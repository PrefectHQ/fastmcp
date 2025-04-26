import base64
import json
import re

import httpx
import pytest
from dirty_equals import IsStr
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from mcp.types import BlobResourceContents, TextContent, TextResourceContents
from pydantic import BaseModel, TypeAdapter
from pydantic.networks import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.openapi import FastMCPOpenAPI


class User(BaseModel):
    id: int
    name: str
    active: bool


class UserCreate(BaseModel):
    name: str
    active: bool


@pytest.fixture
def users_db() -> dict[int, User]:
    return {
        1: User(id=1, name="Alice", active=True),
        2: User(id=2, name="Bob", active=True),
        3: User(id=3, name="Charlie", active=False),
    }


@pytest.fixture
def fastapi_app(users_db: dict[int, User]) -> FastAPI:
    app = FastAPI(title="FastAPI App")

    @app.get("/users", tags=["users", "list"])
    async def get_users() -> list[User]:
        """Get all users."""
        return sorted(users_db.values(), key=lambda x: x.id)

    @app.get("/users/{user_id}", tags=["users", "detail"])
    async def get_user(user_id: int) -> User | None:
        """Get a user by ID."""
        return users_db.get(user_id)

    @app.post("/users", tags=["users", "create"])
    async def create_user(user: UserCreate) -> User:
        """Create a new user."""
        user_id = max(users_db.keys()) + 1
        new_user = User(id=user_id, **user.model_dump())
        users_db[user_id] = new_user
        return new_user

    @app.patch("/users/{user_id}/name", tags=["users", "update"])
    async def update_user_name(user_id: int, name: str) -> User:
        """Update a user's name."""
        user = users_db.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.name = name
        return user

    @app.get("/ping", response_class=PlainTextResponse)
    async def ping() -> str:
        """Ping the server."""
        return "pong"

    @app.get("/ping-bytes")
    async def ping_bytes() -> Response:
        """Ping the server and get a bytes response."""

        return Response(content=b"pong")

    return app


@pytest.fixture
def api_client(fastapi_app: FastAPI) -> AsyncClient:
    """Create a pre-configured httpx client for testing."""
    return AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")


@pytest.fixture
async def fastmcp_openapi_server(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
) -> FastMCPOpenAPI:
    openapi_spec = fastapi_app.openapi()

    return FastMCPOpenAPI(
        openapi_spec=openapi_spec,
        client=api_client,
        name="Test App",
    )


async def test_create_openapi_server(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
):
    openapi_spec = fastapi_app.openapi()

    server = FastMCPOpenAPI(
        openapi_spec=openapi_spec, client=api_client, name="Test App"
    )

    assert isinstance(server, FastMCP)
    assert server.name == "Test App"


async def test_create_openapi_server_classmethod(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
):
    server = FastMCP.from_openapi(openapi_spec=fastapi_app.openapi(), client=api_client)
    assert isinstance(server, FastMCPOpenAPI)
    assert server.name == "OpenAPI FastMCP"


async def test_create_fastapi_server_classmethod(fastapi_app: FastAPI):
    server = FastMCP.from_fastapi(fastapi_app)
    assert isinstance(server, FastMCPOpenAPI)
    assert server.name == "FastAPI App"


class TestTools:
    async def test_list_tools(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """
        By default, tools exclude GET methods
        """
        async with Client(fastmcp_openapi_server) as client:
            tools = await client.list_tools()
        assert len(tools) == 2

        assert tools[0].model_dump() == dict(
            name="create_user_users_post",
            description=IsStr(regex=r"^Create a new user\..*$", regex_flags=re.DOTALL),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "title": "Name"},
                    "active": {"type": "boolean", "title": "Active"},
                },
                "required": ["name", "active"],
            },
        )
        assert tools[1].model_dump() == dict(
            name="update_user_name_users__user_id__name_patch",
            description=IsStr(
                regex=r"^Update a user's name\..*$", regex_flags=re.DOTALL
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "title": "User Id"},
                    "name": {"type": "string", "title": "Name"},
                },
                "required": ["user_id", "name"],
            },
        )

    async def test_call_create_user_tool(
        self, fastmcp_openapi_server: FastMCPOpenAPI, api_client
    ):
        """
        The tool created by the OpenAPI server should be the same as the original
        """
        async with Client(fastmcp_openapi_server) as client:
            tool_response = await client.call_tool(
                "create_user_users_post", {"name": "David", "active": False}
            )

        # Convert TextContent to dict for comparison
        assert isinstance(tool_response, list) and len(tool_response) == 1
        assert isinstance(tool_response[0], TextContent)

        response_data = json.loads(tool_response[0].text)
        expected_user = User(id=4, name="David", active=False).model_dump()
        assert response_data == expected_user

        # Check that the user was created via API
        response = await api_client.get("/users")
        assert len(response.json()) == 4

        # Check that the user was created via MCP
        async with Client(fastmcp_openapi_server) as client:
            user_response = await client.read_resource(
                "resource://openapi/get_user_users__user_id__get/4"
            )
            assert isinstance(user_response[0], TextResourceContents)
            response_text = user_response[0].text
            user = json.loads(response_text)
        assert user == expected_user

    async def test_call_update_user_name_tool(
        self, fastmcp_openapi_server: FastMCPOpenAPI, api_client
    ):
        """
        The tool created by the OpenAPI server should be the same as the original
        """
        async with Client(fastmcp_openapi_server) as client:
            tool_response = await client.call_tool(
                "update_user_name_users__user_id__name_patch",
                {"user_id": 1, "name": "XYZ"},
            )

        # Convert TextContent to dict for comparison
        assert isinstance(tool_response, list) and len(tool_response) == 1
        assert isinstance(tool_response[0], TextContent)

        response_data = json.loads(tool_response[0].text)
        expected_data = dict(id=1, name="XYZ", active=True)
        assert response_data == expected_data

        # Check that the user was updated via API
        response = await api_client.get("/users")
        assert expected_data in response.json()

        # Check that the user was updated via MCP
        async with Client(fastmcp_openapi_server) as client:
            user_response = await client.read_resource(
                "resource://openapi/get_user_users__user_id__get/1"
            )
            assert isinstance(user_response[0], TextResourceContents)
            response_text = user_response[0].text
            user = json.loads(response_text)
        assert user == expected_data


class TestResources:
    async def test_list_resources(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """
        By default, resources exclude GET methods without parameters
        """
        async with Client(fastmcp_openapi_server) as client:
            resources = await client.list_resources()
        assert len(resources) == 3
        assert resources[0].uri == AnyUrl("resource://openapi/get_users_users_get")
        assert resources[0].name == "get_users_users_get"

    async def test_get_resource(
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
        users_db: dict[int, User],
    ):
        """
        The resource created by the OpenAPI server should be the same as the original
        """

        json_users = TypeAdapter(list[User]).dump_python(
            sorted(users_db.values(), key=lambda x: x.id)
        )
        async with Client(fastmcp_openapi_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/get_users_users_get"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            response_text = resource_response[0].text
            resource = json.loads(response_text)
        assert resource == json_users
        response = await api_client.get("/users")
        assert response.json() == json_users

    async def test_get_bytes_resource(
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
    ):
        """Test reading a resource that returns bytes."""
        async with Client(fastmcp_openapi_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/ping_bytes_ping_bytes_get"
            )
            assert isinstance(resource_response[0], BlobResourceContents)
            assert base64.b64decode(resource_response[0].blob) == b"pong"

    async def test_get_str_resource(
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
    ):
        """Test reading a resource that returns a string."""
        async with Client(fastmcp_openapi_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/ping_ping_get"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            assert resource_response[0].text == "pong"


class TestResourceTemplates:
    async def test_list_resource_templates(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """
        By default, resource templates exclude GET methods without parameters
        """
        async with Client(fastmcp_openapi_server) as client:
            resource_templates = await client.list_resource_templates()
        assert len(resource_templates) == 1
        assert resource_templates[0].name == "get_user_users__user_id__get"
        assert (
            resource_templates[0].uriTemplate
            == r"resource://openapi/get_user_users__user_id__get/{user_id}"
        )

    async def test_get_resource_template(
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
        users_db: dict[int, User],
    ):
        """
        The resource template created by the OpenAPI server should be the same as the original
        """
        user_id = 2
        async with Client(fastmcp_openapi_server) as client:
            resource_response = await client.read_resource(
                f"resource://openapi/get_user_users__user_id__get/{user_id}"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            response_text = resource_response[0].text
            resource = json.loads(response_text)

        assert resource == users_db[user_id].model_dump()
        response = await api_client.get(f"/users/{user_id}")
        assert resource == response.json()


class TestPrompts:
    async def test_list_prompts(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """
        By default, there are no prompts.
        """
        async with Client(fastmcp_openapi_server) as client:
            prompts = await client.list_prompts()
        assert len(prompts) == 0


class TestTagTransfer:
    """Tests for transferring tags from OpenAPI routes to MCP objects."""

    async def test_tags_transferred_to_tools(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """Test that tags from OpenAPI routes are correctly transferred to Tools."""
        # Get internal tools directly (not the public API which returns MCP.Content)
        tools = fastmcp_openapi_server._tool_manager.list_tools()

        # Find the create_user and update_user_name tools
        create_user_tool = next(
            (t for t in tools if t.name == "create_user_users_post"), None
        )
        update_user_tool = next(
            (
                t
                for t in tools
                if t.name == "update_user_name_users__user_id__name_patch"
            ),
            None,
        )

        assert create_user_tool is not None
        assert update_user_tool is not None

        # Check that tags from OpenAPI routes were transferred to the Tool objects
        assert "users" in create_user_tool.tags
        assert "create" in create_user_tool.tags
        assert len(create_user_tool.tags) == 2

        assert "users" in update_user_tool.tags
        assert "update" in update_user_tool.tags
        assert len(update_user_tool.tags) == 2

    async def test_tags_transferred_to_resources(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """Test that tags from OpenAPI routes are correctly transferred to Resources."""
        # Get internal resources directly
        resources = list(
            fastmcp_openapi_server._resource_manager.get_resources().values()
        )

        # Find the get_users resource
        get_users_resource = next(
            (r for r in resources if r.name == "get_users_users_get"), None
        )

        assert get_users_resource is not None

        # Check that tags from OpenAPI routes were transferred to the Resource object
        assert "users" in get_users_resource.tags
        assert "list" in get_users_resource.tags
        assert len(get_users_resource.tags) == 2

    async def test_tags_transferred_to_resource_templates(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """Test that tags from OpenAPI routes are correctly transferred to ResourceTemplates."""
        # Get internal resource templates directly
        templates = list(
            fastmcp_openapi_server._resource_manager.get_templates().values()
        )

        # Find the get_user template
        get_user_template = next(
            (t for t in templates if t.name == "get_user_users__user_id__get"), None
        )

        assert get_user_template is not None

        # Check that tags from OpenAPI routes were transferred to the ResourceTemplate object
        assert "users" in get_user_template.tags
        assert "detail" in get_user_template.tags
        assert len(get_user_template.tags) == 2

    async def test_tags_preserved_in_resources_created_from_templates(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """Test that tags are preserved when creating resources from templates."""
        # Get internal resource templates directly
        templates = list(
            fastmcp_openapi_server._resource_manager.get_templates().values()
        )

        # Find the get_user template
        get_user_template = next(
            (t for t in templates if t.name == "get_user_users__user_id__get"), None
        )

        assert get_user_template is not None

        # Manually create a resource from template
        params = {"user_id": 1}
        resource = await get_user_template.create_resource(
            "resource://openapi/get_user_users__user_id__get/1", params
        )

        # Verify tags are preserved from template to resource
        assert "users" in resource.tags
        assert "detail" in resource.tags
        assert len(resource.tags) == 2


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
    async def openapi_30_server(
        self, openapi_30_spec, mock_30_client
    ) -> FastMCPOpenAPI:
        """Create a FastMCPOpenAPI server from the OpenAPI 3.0 spec."""
        return FastMCPOpenAPI(
            openapi_spec=openapi_30_spec, client=mock_30_client, name="Product API 3.0"
        )

    async def test_server_creation(self, openapi_30_server):
        """Test that a server can be created from an OpenAPI 3.0 spec."""
        assert isinstance(openapi_30_server, FastMCP)
        assert openapi_30_server.name == "Product API 3.0"

    async def test_resource_discovery(self, openapi_30_server):
        """Test that resources are correctly discovered from an OpenAPI 3.0 spec."""
        async with Client(openapi_30_server) as client:
            resources = await client.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == AnyUrl("resource://openapi/listProducts")

    async def test_resource_template_discovery(self, openapi_30_server):
        """Test that resource templates are correctly discovered from an OpenAPI 3.0 spec."""
        async with Client(openapi_30_server) as client:
            templates = await client.list_resource_templates()
        assert len(templates) == 1
        assert templates[0].name == "getProduct"
        assert templates[0].uriTemplate == r"resource://openapi/getProduct/{product_id}"

    async def test_tool_discovery(self, openapi_30_server):
        """Test that tools are correctly discovered from an OpenAPI 3.0 spec."""
        async with Client(openapi_30_server) as client:
            tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "createProduct"
        assert "name" in tools[0].inputSchema["properties"]
        assert "price" in tools[0].inputSchema["properties"]

    async def test_resource_access(self, openapi_30_server):
        """Test reading a resource from an OpenAPI 3.0 server."""
        async with Client(openapi_30_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/listProducts"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            response_text = resource_response[0].text
            content = json.loads(response_text)
        assert len(content) == 2
        assert content[0]["name"] == "Product 1"
        assert content[1]["name"] == "Product 2"

    async def test_resource_template_access(self, openapi_30_server):
        """Test reading a resource from template from an OpenAPI 3.0 server."""
        async with Client(openapi_30_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/getProduct/p1"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            response_text = resource_response[0].text
            content = json.loads(response_text)
        assert content["id"] == "p1"
        assert content["name"] == "Product 1"
        assert content["price"] == 19.99

    async def test_tool_execution(self, openapi_30_server):
        """Test executing a tool from an OpenAPI 3.0 server."""
        async with Client(openapi_30_server) as client:
            result = await client.call_tool(
                "createProduct", {"name": "New Product", "price": 39.99}
            )
            # Result should be a text content
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            product = json.loads(result[0].text)
            assert product["id"] == "p3"
            assert product["name"] == "New Product"
            assert product["price"] == 39.99


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
    async def openapi_31_server(
        self, openapi_31_spec, mock_31_client
    ) -> FastMCPOpenAPI:
        """Create a FastMCPOpenAPI server from the OpenAPI 3.1 spec."""
        return FastMCPOpenAPI(
            openapi_spec=openapi_31_spec, client=mock_31_client, name="Order API 3.1"
        )

    async def test_server_creation(self, openapi_31_server):
        """Test that a server can be created from an OpenAPI 3.1 spec."""
        assert isinstance(openapi_31_server, FastMCP)
        assert openapi_31_server.name == "Order API 3.1"

    async def test_resource_discovery(self, openapi_31_server):
        """Test that resources are correctly discovered from an OpenAPI 3.1 spec."""
        async with Client(openapi_31_server) as client:
            resources = await client.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == AnyUrl("resource://openapi/listOrders")

    async def test_resource_template_discovery(self, openapi_31_server):
        """Test that resource templates are correctly discovered from an OpenAPI 3.1 spec."""
        async with Client(openapi_31_server) as client:
            templates = await client.list_resource_templates()
        assert len(templates) == 1
        assert templates[0].name == "getOrder"
        assert templates[0].uriTemplate == r"resource://openapi/getOrder/{order_id}"

    async def test_tool_discovery(self, openapi_31_server):
        """Test that tools are correctly discovered from an OpenAPI 3.1 spec."""
        async with Client(openapi_31_server) as client:
            tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "createOrder"
        assert "customer" in tools[0].inputSchema["properties"]
        assert "items" in tools[0].inputSchema["properties"]

    async def test_resource_access(self, openapi_31_server):
        """Test reading a resource from an OpenAPI 3.1 server."""
        async with Client(openapi_31_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/listOrders"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            response_text = resource_response[0].text
            content = json.loads(response_text)
        assert len(content) == 2
        assert content[0]["customer"] == "Alice"
        assert content[1]["customer"] == "Bob"

    async def test_resource_template_access(self, openapi_31_server):
        """Test reading a resource from template from an OpenAPI 3.1 server."""
        async with Client(openapi_31_server) as client:
            resource_response = await client.read_resource(
                "resource://openapi/getOrder/o1"
            )
            assert isinstance(resource_response[0], TextResourceContents)
            response_text = resource_response[0].text
            content = json.loads(response_text)
        assert content["id"] == "o1"
        assert content["customer"] == "Alice"
        assert content["items"] == ["item1", "item2"]

    async def test_tool_execution(self, openapi_31_server):
        """Test executing a tool from an OpenAPI 3.1 server."""
        async with Client(openapi_31_server) as client:
            result = await client.call_tool(
                "createOrder", {"customer": "Charlie", "items": ["item4", "item5"]}
            )
            # Result should be a text content
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            order = json.loads(result[0].text)
            assert order["id"] == "o3"
            assert order["customer"] == "Charlie"
            assert order["items"] == ["item4", "item5"]


class TestMountFastMCP:
    """Tests for mounting FastMCP servers."""

    async def test_mount_fastmcp(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """Test mounting an OpenAPI server."""
        mcp = FastMCP("MainApp")

        await mcp.import_server("fastapi", fastmcp_openapi_server)

        # Check that resources are available with prefixed URIs
        async with Client(mcp) as client:
            resources = await client.list_resources()
        assert len(resources) == 3
        # We're checking the key used by mcp to store the resource
        # The prefixed URI is used as the key, but the resource's original uri is preserved
        prefixed_uri = "fastapi+resource://openapi/get_users_users_get"
        resource = mcp._resource_manager.get_resources().get(prefixed_uri)
        assert resource is not None

        # Check that templates are available with prefixed URIs
        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
        assert len(templates) == 1
        assert templates[0].name == "get_user_users__user_id__get"
        prefixed_template_uri = (
            r"fastapi+resource://openapi/get_user_users__user_id__get/{user_id}"
        )
        template = mcp._resource_manager.get_templates().get(prefixed_template_uri)
        assert template is not None

        # Check that tools are available with prefixed names
        async with Client(mcp) as client:
            tools = await client.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "fastapi_create_user_users_post"
        assert tools[1].name == "fastapi_update_user_name_users__user_id__name_patch"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
        assert len(prompts) == 0
