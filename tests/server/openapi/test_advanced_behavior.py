from enum import Enum
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.openapi import FastMCPOpenAPI, MCPType, RouteMap


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
        with pytest.raises(
            ToolError, match="Input validation error|Missing required path parameters"
        ):
            await client.call_tool(
                "update_user_name_users",
                {
                    "user_id": None,  # This should cause an error
                    "name": "New Name",
                },
            )


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
