import base64
import json
import re

import httpx
from dirty_equals import IsStr
from fastapi import FastAPI
from mcp.types import BlobResourceContents
from pydantic import TypeAdapter
from pydantic.networks import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.openapi import (
    FastMCPOpenAPI,
    MCPType,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    RouteMap,
)

from .conftest import GET_ROUTE_MAPS, User


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
            meta=None,
            title=None,
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
            outputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "title": "Id"},
                    "name": {"type": "string", "title": "Name"},
                    "active": {"type": "boolean", "title": "Active"},
                },
                "required": ["id", "name", "active"],
                "title": "User",
            },
        )
        assert tools[1].model_dump() == dict(
            name="update_user_name_users",
            meta=None,
            title=None,
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
            outputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "title": "Id"},
                    "name": {"type": "string", "title": "Name"},
                    "active": {"type": "boolean", "title": "Active"},
                },
                "required": ["id", "name", "active"],
                "title": "User",
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

        expected_user = User(id=4, name="David", active=False)
        # Compare the data content since MCP client creates different class instances
        assert tool_response.data.id == expected_user.id
        assert tool_response.data.name == expected_user.name
        assert tool_response.data.active == expected_user.active

        # Check that the user was created via API
        response = await api_client.get("/users")
        assert len(response.json()) == 4

        # Check that the user was created via MCP
        async with Client(fastmcp_openapi_server) as client:
            user_response = await client.read_resource("resource://get_user_users/4")
            response_text = user_response[0].text  # type: ignore[attr-defined]
            user = json.loads(response_text)
        assert user == expected_user.model_dump()

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

        expected_user = User(id=1, name="XYZ", active=True)
        # Compare the data content since MCP client creates different class instances
        assert tool_response.data.id == expected_user.id
        assert tool_response.data.name == expected_user.name
        assert tool_response.data.active == expected_user.active

        # Check that the user was updated via API
        response = await api_client.get("/users")
        assert expected_user.model_dump() in response.json()

        # Check that the user was updated via MCP
        async with Client(fastmcp_openapi_server) as client:
            user_response = await client.read_resource("resource://get_user_users/1")
            response_text = user_response[0].text  # type: ignore[attr-defined]
            user = json.loads(response_text)
        assert user == expected_user.model_dump()

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
            # The tool response should now be unwrapped since we have output schema
            assert tool_response.data == [
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
