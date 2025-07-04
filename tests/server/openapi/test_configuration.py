import httpx
import pytest
from fastapi import FastAPI

from fastmcp import FastMCP
from fastmcp.server.openapi import FastMCPOpenAPI, MCPType, RouteMap

from .conftest import GET_ROUTE_MAPS


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
