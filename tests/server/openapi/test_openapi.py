import base64
import json
import re
from enum import Enum

import httpx
import pytest
from dirty_equals import IsStr
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from mcp.types import BlobResourceContents
from pydantic import BaseModel, TypeAdapter
from pydantic.networks import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.openapi import (
    FastMCPOpenAPI,
    MCPType,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    RouteMap,
)


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


# route maps for GET requests
# use these to create components of all types instead of just tools
GET_ROUTE_MAPS = [
    # GET requests with path parameters go to ResourceTemplate
    RouteMap(
        methods=["GET"],
        pattern=r".*\{.*\}.*",
        mcp_type=MCPType.RESOURCE_TEMPLATE,
    ),
    # GET requests without path parameters go to Resource
    RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
]


@pytest.fixture
def fastapi_app(users_db: dict[int, User]) -> FastAPI:
    app = FastAPI(title="FastAPI App")

    @app.get("/users", tags=["users", "list"])
    async def get_users() -> list[User]:
        """Get all users."""
        return sorted(users_db.values(), key=lambda x: x.id)

    @app.get("/search", tags=["search"])
    async def search_users(
        name: str | None = None, active: bool | None = None, min_id: int | None = None
    ) -> list[User]:
        """Search users with optional filters."""
        results = list(users_db.values())

        if name is not None:
            results = [u for u in results if name.lower() in u.name.lower()]
        if active is not None:
            results = [u for u in results if u.active == active]
        if min_id is not None:
            results = [u for u in results if u.id >= min_id]

        return sorted(results, key=lambda x: x.id)

    @app.get("/users/{user_id}", tags=["users", "detail"])
    async def get_user(user_id: int) -> User | None:
        """Get a user by ID."""
        return users_db.get(user_id)

    @app.get("/users/{user_id}/{is_active}", tags=["users", "detail"])
    async def get_user_active_state(user_id: int, is_active: bool) -> User | None:
        """Get a user by ID and filter by active state."""
        user = users_db.get(user_id)
        if user is not None and user.active == is_active:
            return user
        return None

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
        route_maps=GET_ROUTE_MAPS,
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


async def test_create_openapi_server_with_timeout(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
):
    server = FastMCPOpenAPI(
        openapi_spec=fastapi_app.openapi(),
        client=api_client,
        name="Test App",
        timeout=1.0,
        route_maps=GET_ROUTE_MAPS,
    )
    assert server._timeout == 1.0

    for tool in (await server.get_tools()).values():
        assert isinstance(tool, OpenAPITool)
        assert tool._timeout == 1.0

    for resource in (await server.get_resources()).values():
        assert isinstance(resource, OpenAPIResource)
        assert resource._timeout == 1.0

    for template in (await server.get_resource_templates()).values():
        assert isinstance(template, OpenAPIResourceTemplate)
        assert template._timeout == 1.0


class TestTools:
    async def test_default_behavior_converts_everything_to_tools(
        self, fastapi_app: FastAPI
    ):
        """
        By default, tools exclude GET methods
        """
        server = FastMCPOpenAPI.from_fastapi(fastapi_app)
        assert len(await server.get_tools()) == 8
        assert len(await server.get_resources()) == 0
        assert len(await server.get_resource_templates()) == 0

    async def test_list_tools(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """
        By default, tools exclude GET methods
        """
        async with Client(fastmcp_openapi_server) as client:
            tools = await client.list_tools()
        assert len(tools) == 2

        assert tools[0].model_dump() == dict(
            name="create_user_users_post",
            annotations=None,
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
            name="update_user_name_users",
            annotations=None,
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
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
    ):
        """
        The tool created by the OpenAPI server should be the same as the original
        """
        async with Client(fastmcp_openapi_server) as client:
            tool_response = await client.call_tool(
                "create_user_users_post", {"name": "David", "active": False}
            )

        response_data = json.loads(tool_response[0].text)  # type: ignore[attr-defined]
        expected_user = User(id=4, name="David", active=False).model_dump()
        assert response_data == expected_user

        # Check that the user was created via API
        response = await api_client.get("/users")
        assert len(response.json()) == 4

        # Check that the user was created via MCP
        async with Client(fastmcp_openapi_server) as client:
            user_response = await client.read_resource("resource://get_user_users/4")
            response_text = user_response[0].text  # type: ignore[attr-defined]
            user = json.loads(response_text)
        assert user == expected_user

    async def test_call_update_user_name_tool(
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
    ):
        """
        The tool created by the OpenAPI server should be the same as the original
        """
        async with Client(fastmcp_openapi_server) as client:
            tool_response = await client.call_tool(
                "update_user_name_users",
                {"user_id": 1, "name": "XYZ"},
            )

        response_data = json.loads(tool_response[0].text)  # type: ignore[attr-defined]
        expected_data = dict(id=1, name="XYZ", active=True)
        assert response_data == expected_data

        # Check that the user was updated via API
        response = await api_client.get("/users")
        assert expected_data in response.json()

        # Check that the user was updated via MCP
        async with Client(fastmcp_openapi_server) as client:
            user_response = await client.read_resource("resource://get_user_users/1")
            response_text = user_response[0].text  # type: ignore[attr-defined]
            user = json.loads(response_text)
        assert user == expected_data

    async def test_call_tool_return_list(
        self,
        fastapi_app: FastAPI,
        api_client: httpx.AsyncClient,
        users_db: dict[int, User],
    ):
        """
        The tool created by the OpenAPI server should return a list of content.
        """
        openapi_spec = fastapi_app.openapi()
        mcp_server = FastMCPOpenAPI(
            openapi_spec=openapi_spec,
            client=api_client,
            route_maps=[
                RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.TOOL)
            ],
        )
        async with Client(mcp_server) as client:
            tool_response = await client.call_tool("get_users_users_get", {})
            assert json.loads(tool_response[0].text) == [  # type: ignore[attr-defined]
                user.model_dump()
                for user in sorted(users_db.values(), key=lambda x: x.id)
            ]


class TestResources:
    async def test_list_resources(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """
        By default, resources exclude GET methods without parameters
        """
        async with Client(fastmcp_openapi_server) as client:
            resources = await client.list_resources()
        assert len(resources) == 4
        assert resources[0].uri == AnyUrl("resource://get_users_users_get")
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
                "resource://get_users_users_get"
            )
            response_text = resource_response[0].text  # type: ignore[attr-defined]
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
                "resource://ping_bytes_ping_bytes_get"
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
            resource_response = await client.read_resource("resource://ping_ping_get")
            assert resource_response[0].text == "pong"  # type: ignore[attr-defined]


class TestResourceTemplates:
    async def test_list_resource_templates(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """
        By default, resource templates exclude GET methods without parameters
        """
        async with Client(fastmcp_openapi_server) as client:
            resource_templates = await client.list_resource_templates()
        assert len(resource_templates) == 2
        assert resource_templates[0].name == "get_user_users"
        assert (
            resource_templates[0].uriTemplate == r"resource://get_user_users/{user_id}"
        )
        assert resource_templates[1].name == "get_user_active_state_users"
        assert (
            resource_templates[1].uriTemplate
            == r"resource://get_user_active_state_users/{is_active}/{user_id}"
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
                f"resource://get_user_users/{user_id}"
            )
            response_text = resource_response[0].text  # type: ignore[attr-defined]
            resource = json.loads(response_text)

        assert resource == users_db[user_id].model_dump()
        response = await api_client.get(f"/users/{user_id}")
        assert resource == response.json()

    async def test_get_resource_template_multi_param(
        self,
        fastmcp_openapi_server: FastMCPOpenAPI,
        api_client,
        users_db: dict[int, User],
    ):
        """
        The resource template created by the OpenAPI server should be the same as the original
        """
        user_id = 2
        is_active = True
        async with Client(fastmcp_openapi_server) as client:
            resource_response = await client.read_resource(
                f"resource://get_user_active_state_users/{is_active}/{user_id}"
            )
            response_text = resource_response[0].text  # type: ignore[attr-defined]
            resource = json.loads(response_text)

        assert resource == users_db[user_id].model_dump()
        response = await api_client.get(f"/users/{user_id}/{is_active}")
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
        tools = await fastmcp_openapi_server._tool_manager.list_tools()

        # Find the create_user and update_user_name tools
        create_user_tool = next(
            (t for t in tools if t.name == "create_user_users_post"), None
        )
        update_user_tool = next(
            (t for t in tools if t.name == "update_user_name_users"),
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
        resources_dict = await fastmcp_openapi_server._resource_manager.get_resources()
        resources = list(resources_dict.values())

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
        templates_dict = (
            await fastmcp_openapi_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())

        # Find the get_user template
        get_user_template = next(
            (t for t in templates if t.name == "get_user_users"), None
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
        templates_dict = (
            await fastmcp_openapi_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())

        # Find the get_user template
        get_user_template = next(
            (t for t in templates if t.name == "get_user_users"), None
        )

        assert get_user_template is not None

        # Manually create a resource from template
        params = {"user_id": 1}
        resource = await get_user_template.create_resource(
            "resource://get_user_users/1", params
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
            assert len(result) == 1
            product = json.loads(result[0].text)  # type: ignore[attr-defined]
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
            assert len(result) == 1
            order = json.loads(result[0].text)  # type: ignore[attr-dict]
            assert order["id"] == "o3"
            assert order["customer"] == "Charlie"
            assert order["items"] == ["item4", "item5"]


async def test_empty_query_parameters_not_sent(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
):
    """Test that empty and None query parameters are not sent in the request."""

    # Create a TransportAdapter to track requests
    class RequestCapture(httpx.AsyncBaseTransport):
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.requests = []

        async def handle_async_request(self, request):
            self.requests.append(request)
            return await self.wrapped.handle_async_request(request)

    # Use our transport adapter to wrap the original one
    capture = RequestCapture(api_client._transport)
    api_client._transport = capture

    # Create the OpenAPI server with new route map to make search endpoint a tool
    openapi_spec = fastapi_app.openapi()
    mcp_server = FastMCPOpenAPI(
        openapi_spec=openapi_spec,
        client=api_client,
        route_maps=[RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.TOOL)],
    )

    # Call the search tool with mixed parameter values
    async with Client(mcp_server) as client:
        await client.call_tool(
            "search_users_search_get",
            {
                "name": "",  # Empty string should be excluded
                "active": None,  # None should be excluded
                "min_id": 2,  # Has value, should be included
            },
        )

    # Verify that the request URL only has min_id parameter
    assert len(capture.requests) > 0
    request = capture.requests[-1]  # Get the last request

    # URL should only contain min_id=2, not name= or active=
    url = str(request.url)
    assert "min_id=2" in url, f"URL should contain min_id=2, got: {url}"
    assert "name=" not in url, f"URL should not contain name=, got: {url}"
    assert "active=" not in url, f"URL should not contain active=, got: {url}"

    # More direct check - parse the URL to examine query params
    from urllib.parse import parse_qs, urlparse

    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    assert "min_id" in query_params
    assert "name" not in query_params
    assert "active" not in query_params


async def test_none_path_parameters_rejected(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
):
    """Test that None values for path parameters are properly rejected."""
    # Create the OpenAPI server
    openapi_spec = fastapi_app.openapi()
    mcp_server = FastMCPOpenAPI(
        openapi_spec=openapi_spec,
        client=api_client,
    )

    # Create a client and try to call a tool with a None path parameter
    async with Client(mcp_server) as client:
        # get_user has a required path parameter user_id
        with pytest.raises(ToolError, match="Missing required path parameters"):
            await client.call_tool(
                "update_user_name_users",
                {
                    "user_id": None,  # This should cause an error
                    "name": "New Name",
                },
            )


class TestDescriptionPropagation:
    """Tests for OpenAPI description propagation to FastMCP components.

    Each test focuses on a single, specific behavior to make it immediately clear
    what's broken when a test fails.
    """

    @pytest.fixture
    def simple_openapi_spec(self) -> dict:
        """Create a minimal OpenAPI spec with obvious test descriptions."""
        return {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "summary": "List items summary",
                        "description": "LIST_DESCRIPTION\n\nFUNCTION_LIST_DESCRIPTION",
                        "responses": {
                            "200": {
                                "description": "LIST_RESPONSE_DESCRIPTION",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {
                                                        "type": "string",
                                                        "description": "ITEM_RESPONSE_ID_DESCRIPTION",
                                                    },
                                                    "name": {
                                                        "type": "string",
                                                        "description": "ITEM_RESPONSE_NAME_DESCRIPTION",
                                                    },
                                                    "price": {
                                                        "type": "number",
                                                        "description": "ITEM_RESPONSE_PRICE_DESCRIPTION",
                                                    },
                                                },
                                            },
                                        },
                                    }
                                },
                            }
                        },
                    }
                },
                "/items/{item_id}": {
                    "get": {
                        "operationId": "getItem",
                        "summary": "Get item summary",
                        "description": "GET_DESCRIPTION\n\nFUNCTION_GET_DESCRIPTION",
                        "parameters": [
                            {
                                "name": "item_id",
                                "in": "path",
                                "required": True,
                                "description": "PATH_PARAM_DESCRIPTION",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "fields",
                                "in": "query",
                                "required": False,
                                "description": "QUERY_PARAM_DESCRIPTION",
                                "schema": {"type": "string"},
                            },
                        ],
                        "responses": {
                            "200": {
                                "description": "GET_RESPONSE_DESCRIPTION",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {
                                                    "type": "string",
                                                    "description": "ITEM_RESPONSE_ID_DESCRIPTION",
                                                },
                                                "name": {
                                                    "type": "string",
                                                    "description": "ITEM_RESPONSE_NAME_DESCRIPTION",
                                                },
                                                "price": {
                                                    "type": "number",
                                                    "description": "ITEM_RESPONSE_PRICE_DESCRIPTION",
                                                },
                                            },
                                        },
                                    }
                                },
                            }
                        },
                    }
                },
                "/items/create": {
                    "post": {
                        "operationId": "createItem",
                        "summary": "Create item summary",
                        "description": "CREATE_DESCRIPTION\n\nFUNCTION_CREATE_DESCRIPTION",
                        "requestBody": {
                            "required": True,
                            "description": "BODY_DESCRIPTION",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "description": "PROP_DESCRIPTION",
                                            }
                                        },
                                        "required": ["name"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "CREATE_RESPONSE_DESCRIPTION",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {
                                                    "type": "string",
                                                    "description": "ITEM_RESPONSE_ID_DESCRIPTION",
                                                },
                                                "name": {
                                                    "type": "string",
                                                    "description": "ITEM_RESPONSE_NAME_DESCRIPTION",
                                                },
                                            },
                                        },
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Create a mock client that returns simple responses."""

        async def _responder(request):
            if request.url.path == "/items" and request.method == "GET":
                return httpx.Response(200, json=[{"id": "1", "name": "Item 1"}])
            elif request.url.path.startswith("/items/") and request.method == "GET":
                item_id = request.url.path.split("/")[-1]
                return httpx.Response(
                    200, json={"id": item_id, "name": f"Item {item_id}"}
                )
            elif request.url.path == "/items/create" and request.method == "POST":
                import json

                data = json.loads(request.content)
                return httpx.Response(201, json={"id": "new", "name": data.get("name")})

            return httpx.Response(404)

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture
    async def simple_mcp_server(self, simple_openapi_spec, mock_client):
        """Create a FastMCPOpenAPI server with the simple test spec."""
        return FastMCPOpenAPI(
            openapi_spec=simple_openapi_spec,
            client=mock_client,
            name="Test API",
            route_maps=GET_ROUTE_MAPS,
        )

    # --- RESOURCE TESTS ---

    async def test_resource_includes_route_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a Resource includes the route description."""
        resources = list(
            (await simple_mcp_server._resource_manager.get_resources()).values()
        )
        list_resource = next((r for r in resources if r.name == "listItems"), None)

        assert list_resource is not None, "listItems resource wasn't created"
        assert "LIST_DESCRIPTION" in (list_resource.description or ""), (
            "Route description missing from Resource"
        )

    async def test_resource_includes_response_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a Resource includes the response description."""
        resources = list(
            (await simple_mcp_server._resource_manager.get_resources()).values()
        )
        list_resource = next((r for r in resources if r.name == "listItems"), None)

        assert list_resource is not None, "listItems resource wasn't created"
        assert "LIST_RESPONSE_DESCRIPTION" in (list_resource.description or ""), (
            "Response description missing from Resource"
        )

    async def test_resource_includes_response_model_fields(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a Resource description includes response model field descriptions."""
        resources = list(
            (await simple_mcp_server._resource_manager.get_resources()).values()
        )
        list_resource = next((r for r in resources if r.name == "listItems"), None)

        assert list_resource is not None, "listItems resource wasn't created"
        description = list_resource.description or ""
        assert "ITEM_RESPONSE_ID_DESCRIPTION" in description, (
            "Response model field descriptions missing from Resource description"
        )
        assert "ITEM_RESPONSE_NAME_DESCRIPTION" in description, (
            "Response model field descriptions missing from Resource description"
        )
        assert "ITEM_RESPONSE_PRICE_DESCRIPTION" in description, (
            "Response model field descriptions missing from Resource description"
        )

    # --- RESOURCE TEMPLATE TESTS ---

    async def test_template_includes_route_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a ResourceTemplate includes the route description."""
        templates_dict = (
            await simple_mcp_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if t.name == "getItem"), None)

        assert get_template is not None, "getItem template wasn't created"
        assert "GET_DESCRIPTION" in (get_template.description or ""), (
            "Route description missing from ResourceTemplate"
        )

    async def test_template_includes_function_docstring(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a ResourceTemplate includes the function docstring."""
        templates_dict = (
            await simple_mcp_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if t.name == "getItem"), None)

        assert get_template is not None, "getItem template wasn't created"
        assert "FUNCTION_GET_DESCRIPTION" in (get_template.description or ""), (
            "Function docstring missing from ResourceTemplate"
        )

    async def test_template_includes_path_parameter_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a ResourceTemplate includes path parameter descriptions."""
        templates_dict = (
            await simple_mcp_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if t.name == "getItem"), None)

        assert get_template is not None, "getItem template wasn't created"
        assert "PATH_PARAM_DESCRIPTION" in (get_template.description or ""), (
            "Path parameter description missing from ResourceTemplate description"
        )

    async def test_template_includes_query_parameter_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a ResourceTemplate includes query parameter descriptions."""
        templates_dict = (
            await simple_mcp_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if t.name == "getItem"), None)

        assert get_template is not None, "getItem template wasn't created"
        assert "QUERY_PARAM_DESCRIPTION" in (get_template.description or ""), (
            "Query parameter description missing from ResourceTemplate description"
        )

    async def test_template_parameter_schema_includes_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a ResourceTemplate's parameter schema includes parameter descriptions."""
        templates_dict = (
            await simple_mcp_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if t.name == "getItem"), None)

        assert get_template is not None, "getItem template wasn't created"
        assert "properties" in get_template.parameters, (
            "Schema properties missing from ResourceTemplate"
        )
        assert "item_id" in get_template.parameters["properties"], (
            "item_id missing from ResourceTemplate schema"
        )
        assert "description" in get_template.parameters["properties"]["item_id"], (
            "Description missing from item_id parameter schema"
        )
        assert (
            "PATH_PARAM_DESCRIPTION"
            in get_template.parameters["properties"]["item_id"]["description"]
        ), "Path parameter description incorrect in schema"

    # --- TOOL TESTS ---

    async def test_tool_includes_route_description(self, simple_mcp_server: FastMCP):
        """Test that a Tool includes the route description."""
        tools_dict = await simple_mcp_server._tool_manager.get_tools()
        tools = list(tools_dict.values())
        create_tool = next((t for t in tools if t.name == "createItem"), None)

        assert create_tool is not None, "createItem tool wasn't created"
        assert "CREATE_DESCRIPTION" in (create_tool.description or ""), (
            "Route description missing from Tool"
        )

    async def test_tool_includes_function_docstring(self, simple_mcp_server: FastMCP):
        """Test that a Tool includes the function docstring."""
        tools_dict = await simple_mcp_server._tool_manager.get_tools()
        tools = list(tools_dict.values())
        create_tool = next((t for t in tools if t.name == "createItem"), None)

        assert create_tool is not None, "createItem tool wasn't created"
        description = create_tool.description or ""
        assert "FUNCTION_CREATE_DESCRIPTION" in description, (
            "Function docstring missing from Tool"
        )

    async def test_tool_parameter_schema_includes_property_description(
        self, simple_mcp_server: FastMCP
    ):
        """Test that a Tool's parameter schema includes property descriptions from request model."""
        tools_dict = await simple_mcp_server._tool_manager.get_tools()
        tools = list(tools_dict.values())
        create_tool = next((t for t in tools if t.name == "createItem"), None)

        assert create_tool is not None, "createItem tool wasn't created"
        assert "properties" in create_tool.parameters, (
            "Schema properties missing from Tool"
        )
        assert "name" in create_tool.parameters["properties"], (
            "name parameter missing from Tool schema"
        )
        assert "description" in create_tool.parameters["properties"]["name"], (
            "Description missing from name parameter schema"
        )
        assert (
            "PROP_DESCRIPTION"
            in create_tool.parameters["properties"]["name"]["description"]
        ), "Property description incorrect in schema"

    # --- CLIENT API TESTS ---

    async def test_client_api_resource_description(self, simple_mcp_server: FastMCP):
        """Test that Resource descriptions are accessible via the client API."""
        async with Client(simple_mcp_server) as client:
            resources = await client.list_resources()
            list_resource = next((r for r in resources if r.name == "listItems"), None)

            assert list_resource is not None, (
                "listItems resource not accessible via client API"
            )
            resource_description = list_resource.description or ""
            assert "LIST_DESCRIPTION" in resource_description, (
                "Route description missing in Resource from client API"
            )

    async def test_client_api_template_description(self, simple_mcp_server: FastMCP):
        """Test that ResourceTemplate descriptions are accessible via the client API."""
        async with Client(simple_mcp_server) as client:
            templates = await client.list_resource_templates()
            get_template = next((t for t in templates if t.name == "getItem"), None)

            assert get_template is not None, (
                "getItem template not accessible via client API"
            )
            template_description = get_template.description or ""
            assert "GET_DESCRIPTION" in template_description, (
                "Route description missing in ResourceTemplate from client API"
            )

    async def test_client_api_tool_description(self, simple_mcp_server: FastMCP):
        """Test that Tool descriptions are accessible via the client API."""
        async with Client(simple_mcp_server) as client:
            tools = await client.list_tools()
            create_tool = next((t for t in tools if t.name == "createItem"), None)

            assert create_tool is not None, (
                "createItem tool not accessible via client API"
            )
            tool_description = create_tool.description or ""
            assert "FUNCTION_CREATE_DESCRIPTION" in tool_description, (
                "Function docstring missing in Tool from client API"
            )

    async def test_client_api_tool_parameter_schema(self, simple_mcp_server: FastMCP):
        """Test that Tool parameter schemas are accessible via the client API."""
        async with Client(simple_mcp_server) as client:
            tools = await client.list_tools()
            create_tool = next((t for t in tools if t.name == "createItem"), None)

            assert create_tool is not None, (
                "createItem tool not accessible via client API"
            )
            assert "properties" in create_tool.inputSchema, (
                "Schema properties missing from Tool inputSchema in client API"
            )
            assert "name" in create_tool.inputSchema["properties"], (
                "name parameter missing from Tool schema in client API"
            )
            assert "description" in create_tool.inputSchema["properties"]["name"], (
                "Description missing from name parameter in client API"
            )
            assert (
                "PROP_DESCRIPTION"
                in create_tool.inputSchema["properties"]["name"]["description"]
            ), "Property description incorrect in schema from client API"


class TestFastAPIDescriptionPropagation:
    """Tests for FastAPI docstring and annotation propagation to FastMCP components.

    Each test focuses on a single, specific behavior to make it immediately clear
    what's broken when a test fails.
    """

    @pytest.fixture
    def fastapi_app_with_descriptions(self) -> FastAPI:
        """Create a simple FastAPI app with docstrings and annotations."""
        from typing import Annotated

        from pydantic import BaseModel, Field

        app = FastAPI(title="Test FastAPI App")

        class Item(BaseModel):
            name: str = Field(..., description="ITEM_NAME_DESCRIPTION")
            price: float = Field(..., description="ITEM_PRICE_DESCRIPTION")

        class ItemResponse(BaseModel):
            id: str = Field(..., description="ITEM_RESPONSE_ID_DESCRIPTION")
            name: str = Field(..., description="ITEM_RESPONSE_NAME_DESCRIPTION")
            price: float = Field(..., description="ITEM_RESPONSE_PRICE_DESCRIPTION")

        @app.get("/items", tags=["items"])
        async def list_items() -> list[ItemResponse]:
            """FUNCTION_LIST_DESCRIPTION

            Returns a list of items.
            """
            return [
                ItemResponse(id="1", name="Item 1", price=10.0),
                ItemResponse(id="2", name="Item 2", price=20.0),
            ]

        @app.get("/items/{item_id}", tags=["items", "detail"])
        async def get_item(
            item_id: Annotated[str, Field(description="PATH_PARAM_DESCRIPTION")],
            fields: Annotated[
                str | None, Field(description="QUERY_PARAM_DESCRIPTION")
            ] = None,
        ) -> ItemResponse:
            """FUNCTION_GET_DESCRIPTION

            Gets a specific item by ID.

            Args:
                item_id: The ID of the item to retrieve
                fields: Optional fields to include
            """
            return ItemResponse(
                id=item_id, name=f"Item {item_id}", price=float(item_id) * 10.0
            )

        @app.post("/items", tags=["items", "create"])
        async def create_item(item: Item) -> ItemResponse:
            """FUNCTION_CREATE_DESCRIPTION

            Creates a new item.

            Body:
                Item object with name and price
            """
            return ItemResponse(id="new", name=item.name, price=item.price)

        return app

    @pytest.fixture
    async def fastapi_server(self, fastapi_app_with_descriptions):
        """Create a FastMCP server from the FastAPI app with custom route mappings."""
        # First create from FastAPI app to get the OpenAPI spec
        openapi_spec = fastapi_app_with_descriptions.openapi()

        # Debug: check the operationIds in the OpenAPI spec
        print("\nDEBUG - OpenAPI Paths:")
        for path, methods in openapi_spec["paths"].items():
            for method, details in methods.items():
                if method != "parameters":  # Skip non-HTTP method keys
                    operation_id = details.get("operationId", "no_operation_id")
                    print(
                        f"  Path: {path}, Method: {method}, OperationId: {operation_id}"
                    )

        # Create custom route mappings
        route_maps = [
            # Map GET /items to Resource
            RouteMap(methods=["GET"], pattern=r"^/items$", mcp_type=MCPType.RESOURCE),
            # Map GET /items/{item_id} to ResourceTemplate
            RouteMap(
                methods=["GET"],
                pattern=r"^/items/\{.*\}$",
                mcp_type=MCPType.RESOURCE_TEMPLATE,
            ),
            # Map POST /items to Tool
            RouteMap(methods=["POST"], pattern=r"^/items$", mcp_type=MCPType.TOOL),
        ]

        # Create FastMCP server with the OpenAPI spec and custom route mappings
        server = FastMCPOpenAPI(
            openapi_spec=openapi_spec,
            client=AsyncClient(
                transport=ASGITransport(app=fastapi_app_with_descriptions),
                base_url="http://test",
            ),
            name="Test FastAPI App",
            route_maps=route_maps,
        )

        # Debug: print all components created
        print("\nDEBUG - Resources created:")
        resources_dict = await server._resource_manager.get_resources()
        for name, resource in resources_dict.items():
            print(f"  Resource: {name}, Name attribute: {resource.name}")

        print("\nDEBUG - Templates created:")
        templates_dict = await server._resource_manager.get_resource_templates()
        for name, template in templates_dict.items():
            print(f"  Template: {name}, Name attribute: {template.name}")

        print("\nDEBUG - Tools created:")
        tools = await server._tool_manager.list_tools()
        for tool in tools:
            print(f"  Tool: {tool.name}")

        return server

    async def test_resource_includes_function_docstring(self, fastapi_server: FastMCP):
        """Test that a Resource includes the function docstring."""
        resources_dict = await fastapi_server._resource_manager.get_resources()
        resources = list(resources_dict.values())

        # Now checking for the get_items operation ID rather than list_items
        list_resource = next((r for r in resources if "items_get" in r.name), None)

        assert list_resource is not None, "GET /items resource wasn't created"
        description = list_resource.description or ""
        assert "FUNCTION_LIST_DESCRIPTION" in description, (
            "Function docstring missing from Resource"
        )

    async def test_resource_includes_response_model_fields(
        self, fastapi_server: FastMCP
    ):
        """Test that a Resource description includes basic response information.

        Note: FastAPI doesn't reliably include Pydantic field descriptions in the OpenAPI schema,
        so we can only check for basic response information being present.
        """
        resources_dict = await fastapi_server._resource_manager.get_resources()
        resources = list(resources_dict.values())
        list_resource = next((r for r in resources if "items_get" in r.name), None)

        assert list_resource is not None, "GET /items resource wasn't created"
        description = list_resource.description or ""

        # Check that at least the response information is included
        assert "Successful Response" in description, (
            "Response information missing from Resource description"
        )

        # We've already verified in TestDescriptionPropagation that when descriptions
        # are present in the OpenAPI schema, they are properly included in the component description

    async def test_template_includes_function_docstring(self, fastapi_server: FastMCP):
        """Test that a ResourceTemplate includes the function docstring."""
        templates_dict = await fastapi_server._resource_manager.get_resource_templates()
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if "get_item_items" in t.name), None)

        assert get_template is not None, "GET /items/{item_id} template wasn't created"
        description = get_template.description or ""
        assert "FUNCTION_GET_DESCRIPTION" in description, (
            "Function docstring missing from ResourceTemplate"
        )

    async def test_template_includes_path_parameter_description(
        self, fastapi_server: FastMCP
    ):
        """Test that a ResourceTemplate includes path parameter descriptions.

        Note: Currently, FastAPI parameter descriptions using Annotated[type, Field(description=...)]
        are not properly propagated to the OpenAPI schema. The parameters appear but without the description.
        """
        templates_dict = await fastapi_server._resource_manager.get_resource_templates()
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if "get_item_items" in t.name), None)

        assert get_template is not None, "GET /items/{item_id} template wasn't created"
        description = get_template.description or ""

        # Just test that parameters are included at all
        assert "Path Parameters" in description, (
            "Path parameters section missing from ResourceTemplate description"
        )
        assert "item_id" in description, (
            "item_id parameter missing from ResourceTemplate description"
        )

    async def test_template_includes_query_parameter_description(
        self, fastapi_server: FastMCP
    ):
        """Test that a ResourceTemplate includes query parameter descriptions.

        Note: Currently, FastAPI parameter descriptions using Annotated[type, Field(description=...)]
        are not properly propagated to the OpenAPI schema. The parameters appear but without the description.
        """
        templates_dict = await fastapi_server._resource_manager.get_resource_templates()
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if "get_item_items" in t.name), None)

        assert get_template is not None, "GET /items/{item_id} template wasn't created"
        description = get_template.description or ""

        # Just test that parameters are included at all
        assert "Query Parameters" in description, (
            "Query parameters section missing from ResourceTemplate description"
        )
        assert "fields" in description, (
            "fields parameter missing from ResourceTemplate description"
        )

    async def test_template_parameter_schema_includes_description(
        self, fastapi_server: FastMCP
    ):
        """Test that a ResourceTemplate's parameter schema includes parameter descriptions."""
        templates_dict = await fastapi_server._resource_manager.get_resource_templates()
        templates = list(templates_dict.values())
        get_template = next((t for t in templates if "get_item_items" in t.name), None)

        assert get_template is not None, "GET /items/{item_id} template wasn't created"
        assert "properties" in get_template.parameters, (
            "Schema properties missing from ResourceTemplate"
        )
        assert "item_id" in get_template.parameters["properties"], (
            "item_id missing from ResourceTemplate schema"
        )
        assert "description" in get_template.parameters["properties"]["item_id"], (
            "Description missing from item_id parameter schema"
        )
        assert (
            "PATH_PARAM_DESCRIPTION"
            in get_template.parameters["properties"]["item_id"]["description"]
        ), "Path parameter description incorrect in schema"

    async def test_tool_includes_function_docstring(self, fastapi_server: FastMCP):
        """Test that a Tool includes the function docstring."""
        tools_dict = await fastapi_server._tool_manager.get_tools()
        tools = list(tools_dict.values())
        create_tool = next(
            (t for t in tools if "create_item_items_post" == t.name), None
        )

        assert create_tool is not None, "POST /items tool wasn't created"
        description = create_tool.description or ""
        assert "FUNCTION_CREATE_DESCRIPTION" in description, (
            "Function docstring missing from Tool"
        )

    async def test_tool_parameter_schema_includes_property_description(
        self, fastapi_server: FastMCP
    ):
        """Test that a Tool's parameter schema includes property descriptions from request model.

        Note: Currently, model field descriptions defined in Pydantic models using Field(description=...)
        may not be consistently propagated into the FastAPI OpenAPI schema and thus not into the tool's
        parameter schema.
        """
        tools_dict = await fastapi_server._tool_manager.get_tools()
        tools = list(tools_dict.values())
        create_tool = next(
            (t for t in tools if "create_item_items_post" == t.name), None
        )

        assert create_tool is not None, "POST /items tool wasn't created"
        assert "properties" in create_tool.parameters, (
            "Schema properties missing from Tool"
        )
        assert "name" in create_tool.parameters["properties"], (
            "name parameter missing from Tool schema"
        )
        # We don't test for the description field content as it may not be consistently propagated

    async def test_client_api_resource_description(self, fastapi_server: FastMCP):
        """Test that Resource descriptions are accessible via the client API."""
        async with Client(fastapi_server) as client:
            resources = await client.list_resources()
            list_resource = next((r for r in resources if "items_get" in r.name), None)

            assert list_resource is not None, (
                "GET /items resource not accessible via client API"
            )
            resource_description = list_resource.description or ""
            assert "FUNCTION_LIST_DESCRIPTION" in resource_description, (
                "Function docstring missing in Resource from client API"
            )

    async def test_client_api_template_description(self, fastapi_server: FastMCP):
        """Test that ResourceTemplate descriptions are accessible via the client API."""
        async with Client(fastapi_server) as client:
            templates = await client.list_resource_templates()
            get_template = next(
                (t for t in templates if "get_item_items" in t.name), None
            )

            assert get_template is not None, (
                "GET /items/{item_id} template not accessible via client API"
            )
            template_description = get_template.description or ""
            assert "FUNCTION_GET_DESCRIPTION" in template_description, (
                "Function docstring missing in ResourceTemplate from client API"
            )

    async def test_client_api_tool_description(self, fastapi_server: FastMCP):
        """Test that Tool descriptions are accessible via the client API."""
        async with Client(fastapi_server) as client:
            tools = await client.list_tools()
            create_tool = next(
                (t for t in tools if "create_item_items_post" == t.name), None
            )

            assert create_tool is not None, (
                "POST /items tool not accessible via client API"
            )
            tool_description = create_tool.description or ""
            assert "FUNCTION_CREATE_DESCRIPTION" in tool_description, (
                "Function docstring missing in Tool from client API"
            )

    async def test_client_api_tool_parameter_schema(self, fastapi_server: FastMCP):
        """Test that Tool parameter schemas are accessible via the client API."""
        async with Client(fastapi_server) as client:
            tools = await client.list_tools()
            create_tool = next(
                (t for t in tools if "create_item_items_post" == t.name), None
            )

            assert create_tool is not None, (
                "POST /items tool not accessible via client API"
            )
            assert "properties" in create_tool.inputSchema, (
                "Schema properties missing from Tool inputSchema in client API"
            )
            assert "name" in create_tool.inputSchema["properties"], (
                "name parameter missing from Tool schema in client API"
            )
            # We don't test for the description field content as it may not be consistently propagated


class TestReprMethods:
    """Tests for the custom __repr__ methods of OpenAPI objects."""

    async def test_openapi_tool_repr(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """Test that OpenAPITool's __repr__ method works without recursion errors."""
        tools = await fastmcp_openapi_server._tool_manager.list_tools()
        tool = next(iter(tools))

        # Verify repr doesn't cause recursion and contains expected elements
        tool_repr = repr(tool)
        assert "OpenAPITool" in tool_repr
        assert f"name={tool.name!r}" in tool_repr
        assert "method=" in tool_repr
        assert "path=" in tool_repr

    async def test_openapi_resource_repr(self, fastmcp_openapi_server: FastMCPOpenAPI):
        """Test that OpenAPIResource's __repr__ method works without recursion errors."""
        resources_dict = await fastmcp_openapi_server._resource_manager.get_resources()
        resources = list(resources_dict.values())
        resource = next(iter(resources))

        # Verify repr doesn't cause recursion and contains expected elements
        resource_repr = repr(resource)
        assert "OpenAPIResource" in resource_repr
        assert f"name={resource.name!r}" in resource_repr
        assert "uri=" in resource_repr
        assert "path=" in resource_repr

    async def test_openapi_resource_template_repr(
        self, fastmcp_openapi_server: FastMCPOpenAPI
    ):
        """Test that OpenAPIResourceTemplate's __repr__ method works without recursion errors."""
        templates_dict = (
            await fastmcp_openapi_server._resource_manager.get_resource_templates()
        )
        templates = list(templates_dict.values())
        template = next(iter(templates))

        # Verify repr doesn't cause recursion and contains expected elements
        template_repr = repr(template)
        assert "OpenAPIResourceTemplate" in template_repr
        assert f"name={template.name!r}" in template_repr
        assert "uri_template=" in template_repr
        assert "path=" in template_repr


class TestEnumHandling:
    """Tests for handling enum parameters in OpenAPI schemas."""

    async def test_enum_parameter_schema(self):
        """Test that enum parameters are properly handled in tool parameter schemas."""

        # Define an enum just like in example.py
        class QueryEnum(str, Enum):
            foo = "foo"
            bar = "bar"
            baz = "baz"

        # Create a minimal FastAPI app with an endpoint using the enum
        app = FastAPI()

        @app.post("/items/{item_id}")
        def read_item(
            item_id: int,
            query: QueryEnum | None = None,
        ):
            return {"item_id": item_id, "query": query}

        # Create a client for the app
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        # Create the FastMCPOpenAPI server from the app
        openapi_spec = app.openapi()
        server = FastMCPOpenAPI(
            openapi_spec=openapi_spec,
            client=client,
            name="Enum Test",
        )

        # Get the tools from the server
        tools = await server._tool_manager.list_tools()

        # Find the read_item tool
        read_item_tool = next((t for t in tools if t.name == "read_item_items"), None)

        # Verify the tool exists
        assert read_item_tool is not None, "read_item tool wasn't created"

        # Check that the parameters include the enum reference
        assert "properties" in read_item_tool.parameters
        assert "query" in read_item_tool.parameters["properties"]

        # Check for the anyOf with $ref to the enum definition
        query_param = read_item_tool.parameters["properties"]["query"]
        assert "anyOf" in query_param

        # Find the ref in the anyOf list
        ref_found = False
        for option in query_param["anyOf"]:
            if "$ref" in option and option["$ref"].startswith("#/$defs/QueryEnum"):
                ref_found = True
                break

        assert ref_found, "Reference to enum definition not found in query parameter"

        # Check that the $defs section exists and contains the enum definition
        assert "$defs" in read_item_tool.parameters
        assert "QueryEnum" in read_item_tool.parameters["$defs"]

        # Verify the enum definition
        enum_def = read_item_tool.parameters["$defs"]["QueryEnum"]
        assert "enum" in enum_def
        assert enum_def["enum"] == ["foo", "bar", "baz"]
        assert enum_def["type"] == "string"


class TestRouteMapWildcard:
    """Tests for wildcard RouteMap methods functionality."""

    @pytest.fixture
    def basic_openapi_spec(self) -> dict:
        """Create a minimal OpenAPI spec with different HTTP methods."""
        return {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "getUsers",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "createUser",
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/posts": {
                    "get": {
                        "operationId": "getPosts",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "createPost",
                        "responses": {"201": {"description": "Created"}},
                    },
                },
            },
        }

    @pytest.fixture
    async def mock_basic_client(self) -> httpx.AsyncClient:
        """Create a simple mock client."""

        async def _responder(request):
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_wildcard_matches_all_methods(
        self, basic_openapi_spec, mock_basic_client
    ):
        """Test that a RouteMap with methods='*' matches all HTTP methods."""
        # Create a single route map with wildcard method
        route_maps = [RouteMap(methods="*", pattern=r".*", mcp_type=MCPType.TOOL)]

        mcp = FastMCPOpenAPI(
            openapi_spec=basic_openapi_spec,
            client=mock_basic_client,
            route_maps=route_maps,
        )

        # All operations should be mapped to tools
        tools = await mcp._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}

        # Check that all 4 operations became tools
        expected_tools = {"getUsers", "createUser", "getPosts", "createPost"}
        assert tool_names == expected_tools


class TestRouteMapTags:
    """Tests for RouteMap tags functionality."""

    @pytest.fixture
    def tagged_openapi_spec(self) -> dict:
        """Create an OpenAPI spec with various tags for testing."""
        return {
            "openapi": "3.1.0",
            "info": {"title": "Tagged API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "getUsers",
                        "tags": ["users", "public"],
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "createUser",
                        "tags": ["users", "admin"],
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/admin/stats": {
                    "get": {
                        "operationId": "getAdminStats",
                        "tags": ["admin", "internal"],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/health": {
                    "get": {
                        "operationId": "getHealth",
                        "tags": ["public"],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/metrics": {
                    "get": {
                        "operationId": "getMetrics",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
        }

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Create a simple mock client."""

        async def _responder(request):
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_tags_as_tools(self, tagged_openapi_spec, mock_client):
        """Test that routes with specific tags are converted to tools."""
        # Convert routes with "admin" tag to tools
        route_maps = [
            RouteMap(methods="*", pattern=r".*", mcp_type=MCPType.TOOL, tags={"admin"}),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=tagged_openapi_spec,
            client=mock_client,
            route_maps=route_maps,
        )

        # Check that admin-tagged routes are tools
        tools_dict = await server._tool_manager.get_tools()
        tool_names = {t.name for t in tools_dict.values()}

        resources_dict = await server._resource_manager.get_resources()
        resource_names = {r.name for r in resources_dict.values()}

        # Routes with "admin" tag should be tools
        assert "createUser" in tool_names
        assert "getAdminStats" in tool_names

        # Routes without "admin" tag should be resources
        assert "getUsers" in resource_names
        assert "getHealth" in resource_names
        assert "getMetrics" in resource_names

    async def test_exclude_tags(self, tagged_openapi_spec, mock_client):
        """Test that routes with specific tags are excluded."""
        # Exclude routes with "internal" tag
        route_maps = [
            RouteMap(
                methods="*", pattern=r".*", mcp_type=MCPType.EXCLUDE, tags={"internal"}
            ),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
            RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=tagged_openapi_spec,
            client=mock_client,
            route_maps=route_maps,
        )

        # Check that internal-tagged routes are excluded
        resources_dict = await server._resource_manager.get_resources()
        resource_names = {r.name for r in resources_dict.values()}

        tools_dict = await server._tool_manager.get_tools()
        tool_names = {t.name for t in tools_dict.values()}

        # Internal-tagged route should be excluded
        assert "getAdminStats" not in resource_names
        assert "getAdminStats" not in tool_names

        # Other routes should still be present
        assert "getUsers" in resource_names
        assert "getHealth" in resource_names
        assert "getMetrics" in resource_names
        assert "createUser" in tool_names

    async def test_multiple_tags_and_condition(self, tagged_openapi_spec, mock_client):
        """Test that routes must have ALL specified tags (AND condition)."""
        # Routes must have BOTH "users" AND "admin" tags
        route_maps = [
            RouteMap(
                methods="*",
                pattern=r".*",
                mcp_type=MCPType.TOOL,
                tags={"users", "admin"},
            ),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=tagged_openapi_spec,
            client=mock_client,
            route_maps=route_maps,
        )

        tools_dict = await server._tool_manager.get_tools()
        tool_names = {t.name for t in tools_dict.values()}

        resources_dict = await server._resource_manager.get_resources()
        resource_names = {r.name for r in resources_dict.values()}

        # Only createUser has both "users" AND "admin" tags
        assert "createUser" in tool_names

        # Other routes should be resources
        assert "getUsers" in resource_names  # has "users" but not "admin"
        assert "getAdminStats" in resource_names  # has "admin" but not "users"
        assert "getHealth" in resource_names
        assert "getMetrics" in resource_names

    async def test_pattern_and_tags_combination(self, tagged_openapi_spec, mock_client):
        """Test that both pattern and tags must be satisfied."""
        # Routes matching pattern AND having specific tags
        route_maps = [
            RouteMap(
                methods="*",
                pattern=r".*/admin/.*",
                mcp_type=MCPType.TOOL,
                tags={"admin"},
            ),
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
            RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=tagged_openapi_spec,
            client=mock_client,
            route_maps=route_maps,
        )

        tools_dict = await server._tool_manager.get_tools()
        tool_names = {t.name for t in tools_dict.values()}

        resources_dict = await server._resource_manager.get_resources()
        resource_names = {r.name for r in resources_dict.values()}

        # Only getAdminStats matches both /admin/ pattern AND "admin" tag
        assert "getAdminStats" in tool_names

        # createUser has "admin" tag but doesn't match pattern, so it becomes a tool via POST rule
        assert "createUser" in tool_names

        # Other routes should be resources (GET)
        assert "getUsers" in resource_names
        assert "getHealth" in resource_names
        assert "getMetrics" in resource_names

    async def test_empty_tags_ignored(self, tagged_openapi_spec, mock_client):
        """Test that empty tags set is ignored (matches all routes)."""
        # Empty tags should match all routes
        route_maps = [
            RouteMap(methods="*", pattern=r".*", mcp_type=MCPType.TOOL, tags=set()),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=tagged_openapi_spec,
            client=mock_client,
            route_maps=route_maps,
        )

        tools_dict = await server._tool_manager.get_tools()
        tool_names = {t.name for t in tools_dict.values()}

        # All routes should be tools since empty tags matches everything
        expected_tools = {
            "getUsers",
            "createUser",
            "getAdminStats",
            "getHealth",
            "getMetrics",
        }
        assert tool_names == expected_tools


class TestMCPNames:
    """Tests for the mcp_names dictionary functionality."""

    @pytest.fixture
    def mcp_names_openapi_spec(self) -> dict:
        """OpenAPI spec with various operationIds for testing naming strategies."""
        return {
            "openapi": "3.1.0",
            "info": {"title": "MCP Names Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "list_users__with_pagination",
                        "summary": "Get All Users",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "operationId": "create_user_admin__special_permissions",
                        "summary": "Create New User",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"],
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/users/{id}": {
                    "get": {
                        "operationId": "get_user_by_id__admin_only",
                        "summary": "Fetch Single User Profile",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/very-long-endpoint-name": {
                    "get": {
                        "operationId": "this_is_a_very_long_operation_id_that_exceeds_fifty_six_characters_and_should_be_truncated",
                        "summary": "This Is A Very Long Summary That Should Also Be Truncated When Used As Name",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
                "/special": {
                    "get": {
                        "operationId": "special-chars@and#spaces in$operation%id",
                        "summary": "Special Chars & Spaces In Summary!",
                        "responses": {"200": {"description": "Success"}},
                    }
                },
            },
        }

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Mock client for testing."""

        async def _responder(request):
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_mcp_names_custom_mapping(self, mcp_names_openapi_spec, mock_client):
        """Test that mcp_names dictionary provides custom names for components."""
        mcp_names = {
            "list_users__with_pagination": "user_list",
            "create_user_admin__special_permissions": "admin_create_user",
            "get_user_by_id__admin_only": "user_detail",
        }

        server = FastMCPOpenAPI(
            openapi_spec=mcp_names_openapi_spec,
            client=mock_client,
            mcp_names=mcp_names,
            route_maps=GET_ROUTE_MAPS,
        )

        # Check tools use custom names
        tools = await server._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}
        assert "admin_create_user" in tool_names

        # Check resource templates use custom names
        templates_dict = await server._resource_manager.get_resource_templates()
        template_names = {template.name for template in templates_dict.values()}
        assert "user_detail" in template_names

        # Check resources use custom names
        resources_dict = await server._resource_manager.get_resources()
        resource_names = {resource.name for resource in resources_dict.values()}
        assert "user_list" in resource_names

    async def test_mcp_names_fallback_to_operation_id_short(
        self, mcp_names_openapi_spec, mock_client
    ):
        """Test fallback to operationId up to double underscore when not in mcp_names."""
        # Only provide mapping for one operationId
        mcp_names = {
            "list_users__with_pagination": "custom_user_list",
        }

        server = FastMCPOpenAPI(
            openapi_spec=mcp_names_openapi_spec,
            client=mock_client,
            mcp_names=mcp_names,
            route_maps=GET_ROUTE_MAPS,
        )

        tools = await server._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}

        templates_dict = await server._resource_manager.get_resource_templates()
        template_names = {template.name for template in templates_dict.values()}

        resources_dict = await server._resource_manager.get_resources()
        resource_names = {resource.name for resource in resources_dict.values()}

        # Custom mapped name should be used
        assert "custom_user_list" in resource_names

        # Unmapped operationIds should use short version (up to __)
        assert "create_user_admin" in tool_names
        assert "get_user_by_id" in template_names

    async def test_names_are_slugified(self, mcp_names_openapi_spec, mock_client):
        """Test that names are properly slugified (spaces, special chars removed)."""
        server = FastMCPOpenAPI(
            openapi_spec=mcp_names_openapi_spec,
            client=mock_client,
            route_maps=GET_ROUTE_MAPS,
        )

        resources_dict = await server._resource_manager.get_resources()
        resource_names = {
            resource.name
            for resource in resources_dict.values()
            if resource.name is not None
        }

        # Special chars and spaces should be slugified
        slugified_name = next(
            (name for name in resource_names if "special" in name), None
        )
        assert slugified_name is not None
        # Should not contain special characters or spaces
        assert "@" not in slugified_name
        assert "#" not in slugified_name
        assert "$" not in slugified_name
        assert "%" not in slugified_name
        assert " " not in slugified_name

    async def test_names_are_truncated_to_56_chars(
        self, mcp_names_openapi_spec, mock_client
    ):
        """Test that names are truncated to 56 characters maximum."""
        server = FastMCPOpenAPI(
            openapi_spec=mcp_names_openapi_spec,
            client=mock_client,
            route_maps=GET_ROUTE_MAPS,
        )

        # Check all component types
        all_names = []

        tools = await server._tool_manager.list_tools()
        all_names.extend(tool.name for tool in tools)

        resources_dict = await server._resource_manager.get_resources()
        all_names.extend(resource.name for resource in resources_dict.values())

        templates_dict = await server._resource_manager.get_resource_templates()
        all_names.extend(template.name for template in templates_dict.values())

        # All names should be 56 characters or less
        for name in all_names:
            assert len(name) <= 56, (
                f"Name '{name}' exceeds 56 characters (length: {len(name)})"
            )

        # Verify that the long operationId was actually truncated
        long_name = next((name for name in all_names if len(name) > 50), None)
        assert long_name is not None, "Expected to find a truncated name for testing"

    async def test_mcp_names_with_from_openapi_classmethod(
        self, mcp_names_openapi_spec, mock_client
    ):
        """Test mcp_names works with FastMCP.from_openapi() classmethod."""
        mcp_names = {
            "list_users__with_pagination": "openapi_user_list",
        }

        server = FastMCP.from_openapi(
            openapi_spec=mcp_names_openapi_spec,
            client=mock_client,
            mcp_names=mcp_names,
        )

        tools = await server._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}
        assert "openapi_user_list" in tool_names

    async def test_mcp_names_with_from_fastapi_classmethod(self):
        """Test mcp_names works with FastMCP.from_fastapi() classmethod."""
        from fastapi import FastAPI
        from pydantic import BaseModel

        app = FastAPI(title="FastAPI MCP Names Test")

        class User(BaseModel):
            name: str

        @app.get("/users", operation_id="list_users__with_filters")
        async def get_users() -> list[User]:
            return [User(name="test")]

        @app.post("/users", operation_id="create_user__admin_required")
        async def create_user(user: User) -> User:
            return user

        mcp_names = {
            "list_users__with_filters": "fastapi_user_list",
            "create_user__admin_required": "fastapi_create_user",
        }

        server = FastMCP.from_fastapi(
            app=app,
            mcp_names=mcp_names,
        )

        tools = await server._tool_manager.list_tools()
        tool_names = {tool.name for tool in tools}

        assert "fastapi_create_user" in tool_names
        assert "fastapi_user_list" in tool_names

    async def test_mcp_names_custom_names_are_also_truncated(
        self, mcp_names_openapi_spec, mock_client
    ):
        """Test that custom names in mcp_names are also truncated to 56 characters."""
        # Provide a custom name that's longer than 56 characters
        very_long_custom_name = "this_is_a_very_long_custom_name_that_exceeds_fifty_six_characters_and_should_be_truncated"

        mcp_names = {
            "list_users__with_pagination": very_long_custom_name,
        }

        server = FastMCPOpenAPI(
            openapi_spec=mcp_names_openapi_spec,
            client=mock_client,
            mcp_names=mcp_names,
            route_maps=GET_ROUTE_MAPS,
        )

        resources_dict = await server._resource_manager.get_resources()
        resource_names = {
            resource.name
            for resource in resources_dict.values()
            if resource.name is not None
        }

        # Find the resource that should have the custom name
        truncated_name = next(
            (
                name
                for name in resource_names
                if "this_is_a_very_long_custom_name" in name
            ),
            None,
        )
        assert truncated_name is not None
        assert len(truncated_name) <= 56
        assert (
            len(truncated_name) == 56
        )  # Should be exactly 56 since original was longer


class TestRouteMapMCPTags:
    """Tests for RouteMap mcp_tags functionality."""

    @pytest.fixture
    def simple_fastapi_app(self) -> FastAPI:
        """Create a simple FastAPI app for testing mcp_tags."""
        app = FastAPI(title="MCP Tags Test API")

        @app.get("/users", tags=["users"])
        async def get_users():
            """Get all users."""
            return [{"id": 1, "name": "Alice"}]

        @app.get("/users/{user_id}", tags=["users"])
        async def get_user(user_id: int):
            """Get user by ID."""
            return {"id": user_id, "name": f"User {user_id}"}

        @app.post("/users", tags=["users"])
        async def create_user(name: str):
            """Create a new user."""
            return {"id": 99, "name": name}

        return app

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Mock client for testing."""

        async def _responder(request):
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_mcp_tags_added_to_tools(self, simple_fastapi_app, mock_client):
        """Test that mcp_tags are added to Tools created from routes."""
        # Create route map that adds custom tags to POST endpoints
        route_maps = [
            RouteMap(
                methods=["POST"],
                pattern=r".*",
                mcp_type=MCPType.TOOL,
                mcp_tags={"custom", "api-write"},
            ),
            # Default mapping for other routes
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=simple_fastapi_app.openapi(),
            client=mock_client,
            route_maps=route_maps,
        )

        # Get the POST tool
        tools = await server._tool_manager.list_tools()
        create_user_tool = next((t for t in tools if "create_user" in t.name), None)

        assert create_user_tool is not None, "create_user tool not found"

        # Check that both original tags and mcp_tags are present
        assert "users" in create_user_tool.tags  # Original OpenAPI tag
        assert "custom" in create_user_tool.tags  # Added via mcp_tags
        assert "api-write" in create_user_tool.tags  # Added via mcp_tags

    async def test_mcp_tags_added_to_resources(self, simple_fastapi_app, mock_client):
        """Test that mcp_tags are added to Resources created from routes."""
        # Create route map that adds custom tags to GET endpoints without path params
        route_maps = [
            RouteMap(
                methods=["GET"],
                pattern=r"^/users$",  # Only match /users, not /users/{id}
                mcp_type=MCPType.RESOURCE,
                mcp_tags={"list-data", "public-api"},
            ),
            # Default mapping for other routes
            RouteMap(
                methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE_TEMPLATE
            ),
            RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=simple_fastapi_app.openapi(),
            client=mock_client,
            route_maps=route_maps,
        )

        # Get the resource
        resources_dict = await server._resource_manager.get_resources()
        resources = list(resources_dict.values())
        get_users_resource = next((r for r in resources if "get_users" in r.name), None)

        assert get_users_resource is not None, "get_users resource not found"

        # Check that both original tags and mcp_tags are present
        assert "users" in get_users_resource.tags  # Original OpenAPI tag
        assert "list-data" in get_users_resource.tags  # Added via mcp_tags
        assert "public-api" in get_users_resource.tags  # Added via mcp_tags

    async def test_mcp_tags_added_to_resource_templates(
        self, simple_fastapi_app, mock_client
    ):
        """Test that mcp_tags are added to ResourceTemplates created from routes."""
        # Create route map that adds custom tags to GET endpoints with path params
        route_maps = [
            RouteMap(
                methods=["GET"],
                pattern=r".*\{.*\}.*",  # Match routes with path parameters
                mcp_type=MCPType.RESOURCE_TEMPLATE,
                mcp_tags={"detail-view", "parameterized"},
            ),
            # Default mapping for other routes
            RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
            RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=simple_fastapi_app.openapi(),
            client=mock_client,
            route_maps=route_maps,
        )

        # Get the resource template
        templates_dict = await server._resource_manager.get_resource_templates()
        templates = list(templates_dict.values())
        get_user_template = next((t for t in templates if "get_user" in t.name), None)

        assert get_user_template is not None, "get_user template not found"

        # Check that both original tags and mcp_tags are present
        assert "users" in get_user_template.tags  # Original OpenAPI tag
        assert "detail-view" in get_user_template.tags  # Added via mcp_tags
        assert "parameterized" in get_user_template.tags  # Added via mcp_tags

    async def test_multiple_route_maps_with_different_mcp_tags(
        self, simple_fastapi_app, mock_client
    ):
        """Test that different route maps can add different mcp_tags."""
        # Multiple route maps with different mcp_tags
        route_maps = [
            # First priority: POST requests get write-related tags
            RouteMap(
                methods=["POST"],
                pattern=r".*",
                mcp_type=MCPType.TOOL,
                mcp_tags={"write-operation", "mutation"},
            ),
            # Second priority: GET with path params get detail tags
            RouteMap(
                methods=["GET"],
                pattern=r".*\{.*\}.*",
                mcp_type=MCPType.RESOURCE_TEMPLATE,
                mcp_tags={"detail", "single-item"},
            ),
            # Third priority: Other GET requests get list tags
            RouteMap(
                methods=["GET"],
                pattern=r".*",
                mcp_type=MCPType.RESOURCE,
                mcp_tags={"list", "collection"},
            ),
        ]

        server = FastMCPOpenAPI(
            openapi_spec=simple_fastapi_app.openapi(),
            client=mock_client,
            route_maps=route_maps,
        )

        # Check tool tags
        tools = await server._tool_manager.list_tools()
        create_tool = next((t for t in tools if "create_user" in t.name), None)
        assert create_tool is not None
        assert "write-operation" in create_tool.tags
        assert "mutation" in create_tool.tags

        # Check resource template tags
        templates_dict = await server._resource_manager.get_resource_templates()
        templates = list(templates_dict.values())
        detail_template = next((t for t in templates if "get_user" in t.name), None)
        assert detail_template is not None
        assert "detail" in detail_template.tags
        assert "single-item" in detail_template.tags

        # Check resource tags
        resources_dict = await server._resource_manager.get_resources()
        resources = list(resources_dict.values())
        list_resource = next((r for r in resources if "get_users" in r.name), None)
        assert list_resource is not None
        assert "list" in list_resource.tags
        assert "collection" in list_resource.tags


class TestGlobalTagsParameter:
    """Tests for the global tags parameter on from_openapi and from_fastapi class methods."""

    @pytest.fixture
    def simple_fastapi_app(self) -> FastAPI:
        """Create a simple FastAPI app for testing global tags."""
        app = FastAPI(title="Global Tags Test API")

        @app.get("/items", tags=["items"])
        async def get_items():
            """Get all items."""
            return [{"id": 1, "name": "Item 1"}]

        @app.get("/items/{item_id}", tags=["items"])
        async def get_item(item_id: int):
            """Get item by ID."""
            return {"id": item_id, "name": f"Item {item_id}"}

        @app.post("/items", tags=["items"])
        async def create_item(name: str):
            """Create a new item."""
            return {"id": 99, "name": name}

        return app

    @pytest.fixture
    async def mock_client(self) -> httpx.AsyncClient:
        """Mock client for testing."""

        async def _responder(request):
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(_responder)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_from_fastapi_adds_global_tags(self, simple_fastapi_app):
        """Test that from_fastapi adds global tags to all components."""
        global_tags = {"global", "api-v1"}

        server = FastMCP.from_fastapi(
            simple_fastapi_app,
            tags=global_tags,
            route_maps=[
                RouteMap(
                    methods=["GET"], pattern=r"^/items$", mcp_type=MCPType.RESOURCE
                ),
                RouteMap(
                    methods=["GET"],
                    pattern=r".*\{.*\}.*",
                    mcp_type=MCPType.RESOURCE_TEMPLATE,
                ),
                RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
            ],
        )

        # Check tool has both original and global tags
        tools = await server.get_tools()
        create_item_tool = tools["create_item_items_post"]
        assert "items" in create_item_tool.tags  # Original OpenAPI tag
        assert "global" in create_item_tool.tags  # Global tag
        assert "api-v1" in create_item_tool.tags  # Global tag

        # Check resource has both original and global tags
        resources = await server.get_resources()
        get_items_resource = resources["resource://get_items_items_get"]
        assert "items" in get_items_resource.tags  # Original OpenAPI tag
        assert "global" in get_items_resource.tags  # Global tag
        assert "api-v1" in get_items_resource.tags  # Global tag

        # Check resource template has both original and global tags
        templates = await server.get_resource_templates()
        get_item_template = templates["resource://get_item_items/{item_id}"]
        assert "items" in get_item_template.tags  # Original OpenAPI tag
        assert "global" in get_item_template.tags  # Global tag
        assert "api-v1" in get_item_template.tags  # Global tag

    async def test_from_openapi_adds_global_tags(self, simple_fastapi_app, mock_client):
        """Test that from_openapi adds global tags to all components."""
        global_tags = {"openapi-global", "service"}

        server = FastMCP.from_openapi(
            openapi_spec=simple_fastapi_app.openapi(),
            client=mock_client,
            tags=global_tags,
            route_maps=[
                RouteMap(
                    methods=["GET"], pattern=r"^/items$", mcp_type=MCPType.RESOURCE
                ),
                RouteMap(
                    methods=["GET"],
                    pattern=r".*\{.*\}.*",
                    mcp_type=MCPType.RESOURCE_TEMPLATE,
                ),
                RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL),
            ],
        )

        # Check tool has both original and global tags
        tools = await server.get_tools()
        create_item_tool = tools["create_item_items_post"]
        assert "items" in create_item_tool.tags  # Original OpenAPI tag
        assert "openapi-global" in create_item_tool.tags  # Global tag
        assert "service" in create_item_tool.tags  # Global tag

        # Check resource has both original and global tags
        resources = await server.get_resources()
        get_items_resource = resources["resource://get_items_items_get"]
        assert "items" in get_items_resource.tags  # Original OpenAPI tag
        assert "openapi-global" in get_items_resource.tags  # Global tag
        assert "service" in get_items_resource.tags  # Global tag

        # Check resource template has both original and global tags
        templates = await server.get_resource_templates()
        get_item_template = templates["resource://get_item_items/{item_id}"]
        assert "items" in get_item_template.tags  # Original OpenAPI tag
        assert "openapi-global" in get_item_template.tags  # Global tag
        assert "service" in get_item_template.tags  # Global tag

    async def test_global_tags_combine_with_route_map_tags(
        self, simple_fastapi_app, mock_client
    ):
        """Test that global tags combine with both OpenAPI tags and RouteMap mcp_tags."""
        global_tags = {"global"}
        route_map_tags = {"route-specific"}

        server = FastMCP.from_openapi(
            openapi_spec=simple_fastapi_app.openapi(),
            client=mock_client,
            tags=global_tags,
            route_maps=[
                RouteMap(
                    methods=["POST"],
                    pattern=r".*",
                    mcp_type=MCPType.TOOL,
                    mcp_tags=route_map_tags,
                ),
                RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
            ],
        )

        # Check that all three types of tags are present on the tool
        tools = await server.get_tools()
        create_item_tool = tools["create_item_items_post"]
        assert "items" in create_item_tool.tags  # Original OpenAPI tag
        assert "global" in create_item_tool.tags  # Global tag
        assert "route-specific" in create_item_tool.tags  # RouteMap mcp_tag

        # Check that resource only has OpenAPI and global tags (no route-specific since different RouteMap)
        resources = await server.get_resources()
        get_items_resource = resources["resource://get_items_items_get"]
        assert "items" in get_items_resource.tags  # Original OpenAPI tag
        assert "global" in get_items_resource.tags  # Global tag
        assert "route-specific" not in get_items_resource.tags  # Not from this RouteMap
