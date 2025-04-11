import re

import httpx
import pytest
from dirty_equals import IsStr
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, TypeAdapter
from pydantic.networks import AnyUrl

from fastmcp import FastMCP
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

    @app.get("/users")
    async def get_users() -> list[User]:
        """Get all users."""
        return sorted(users_db.values(), key=lambda x: x.id)

    @app.get("/users/{user_id}")
    async def get_user(user_id: int) -> User | None:
        """Get a user by ID."""
        return users_db.get(user_id)

    @app.post("/users")
    async def create_user(user: UserCreate) -> User:
        """Create a new user."""
        user_id = max(users_db.keys()) + 1
        new_user = User(id=user_id, **user.model_dump())
        users_db[user_id] = new_user
        return new_user

    @app.patch("/users/{user_id}/name")
    async def update_user_name(user_id: int, name: str) -> User:
        """Update a user's name."""
        user = users_db.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.name = name
        return user

    return app


@pytest.fixture
def api_client(fastapi_app: FastAPI) -> AsyncClient:
    """Create a pre-configured httpx client for testing."""
    return AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")


@pytest.fixture
async def fastmcp_server(
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
    async def test_list_tools(self, fastmcp_server: FastMCPOpenAPI):
        """
        By default, tools exclude GET methods
        """
        tools = await fastmcp_server.list_tools()
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
        self, fastmcp_server: FastMCPOpenAPI, api_client
    ):
        """
        The tool created by the OpenAPI server should be the same as the original
        """
        tool_response = await fastmcp_server.call_tool(
            "create_user_users_post", {"name": "David", "active": False}
        )
        assert tool_response == User(id=4, name="David", active=False)

        # Check that the user was created via API

        response = await api_client.get("/users")
        assert len(response.json()) == 4

        # Check that the user was created via MCP
        user_response = await fastmcp_server.read_resource(
            "resource://openapi/get_user_users__user_id__get/4"
        )
        user = user_response[0].content
        assert user == tool_response.model_dump()

    async def test_call_update_user_name_tool(
        self, fastmcp_server: FastMCPOpenAPI, api_client
    ):
        """
        The tool created by the OpenAPI server should be the same as the original
        """
        tool_response = await fastmcp_server.call_tool(
            "update_user_name_users__user_id__name_patch", {"user_id": 1, "name": "XYZ"}
        )
        assert tool_response == dict(id=1, name="XYZ", active=True)

        # Check that the user was updated via API
        response = await api_client.get("/users")
        assert dict(id=1, name="XYZ", active=True) in response.json()

        # Check that the user was updated via MCP
        user_response = await fastmcp_server.read_resource(
            "resource://openapi/get_user_users__user_id__get/1"
        )
        user = user_response[0].content
        assert user == tool_response


class TestResources:
    async def test_list_resources(self, fastmcp_server: FastMCPOpenAPI):
        """
        By default, resources exclude GET methods without parameters
        """
        resources = await fastmcp_server.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == AnyUrl("resource://openapi/get_users_users_get")
        assert resources[0].name == "get_users_users_get"

    async def test_get_resource(
        self, fastmcp_server: FastMCPOpenAPI, api_client, users_db: dict[int, User]
    ):
        """
        The resource created by the OpenAPI server should be the same as the original
        """
        json_users = TypeAdapter(list[User]).dump_python(
            sorted(users_db.values(), key=lambda x: x.id)
        )
        resource_response = await fastmcp_server.read_resource(
            "resource://openapi/get_users_users_get"
        )
        resource = resource_response[0].content
        assert resource == json_users
        response = await api_client.get("/users")
        assert response.json() == json_users


class TestResourceTemplates:
    async def test_list_resource_templates(self, fastmcp_server: FastMCPOpenAPI):
        """
        By default, resource templates exclude GET methods without parameters
        """
        resource_templates = await fastmcp_server.list_resource_templates()
        assert len(resource_templates) == 1
        assert resource_templates[0].name == "get_user_users__user_id__get"
        assert (
            resource_templates[0].uriTemplate
            == r"resource://openapi/get_user_users__user_id__get/{user_id}"
        )

    async def test_get_resource_template(
        self, fastmcp_server: FastMCPOpenAPI, api_client, users_db: dict[int, User]
    ):
        """
        The resource template created by the OpenAPI server should be the same as the original
        """
        user_id = 2
        resource_response = await fastmcp_server.read_resource(
            f"resource://openapi/get_user_users__user_id__get/{user_id}"
        )

        resource = resource_response[0].content
        assert resource == users_db[user_id].model_dump()
        response = await api_client.get(f"/users/{user_id}")
        assert resource == response.json()


class TestPrompts:
    async def test_list_prompts(self, fastmcp_server: FastMCPOpenAPI):
        """
        By default, there are no prompts.
        """
        prompts = await fastmcp_server.list_prompts()
        assert len(prompts) == 0
