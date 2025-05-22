"""Tests for OpenAPI component naming in FastMCP."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from fastmcp.server.openapi import FastMCPOpenAPI, MCPType


@pytest.fixture
def simple_openapi_spec():
    """A simple OpenAPI spec with some routes for testing."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "Get all users",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "summary": "Create a user",
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/users/{id}": {
                "get": {
                    "summary": "Get a user",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "put": {
                    "summary": "Update a user",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/users/{id}/orders": {
                "get": {
                    "summary": "Get user orders",
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
            "/products": {
                "get": {
                    "operationId": "listProducts",
                    "summary": "Get all products",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }


class TestOpenAPIComponentNaming:
    """Tests for OpenAPI component naming functionality."""

    @patch("fastmcp.server.openapi._combine_schemas")
    def test_default_naming(self, mock_combine, simple_openapi_spec):
        """Test the default component naming behavior."""
        # Mock the HTTP client
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Mock the combine schemas function to return empty dict
        mock_combine.return_value = {}

        # Create a server with the default naming
        # Instead of mocking the creation methods, we'll just override them to
        # add the names to _used_names without actually creating components
        class TestServer(FastMCPOpenAPI):
            def _create_openapi_tool(self, route, name):
                _tool_name = self._get_unique_name(name, "tools")
                # Don't actually create the tool, just record that the name was used

            def _create_openapi_resource(self, route, name):
                _resource_name = self._get_unique_name(name, "resources")
                # Don't actually create the resource, just record that the name was used

            def _create_openapi_template(self, route, name):
                _template_name = self._get_unique_name(name, "templates")
                # Don't actually create the template, just record that the name was used

        # Create the server with our test subclass
        server = TestServer(
            openapi_spec=simple_openapi_spec,
            client=mock_client,
        )

        # Check that the correct names were generated
        expected_names = {
            "tools": {"post_users", "put_users"},
            "resources": {
                "users",
                "listProducts",
            },  # GET /users, GET /products (from operationId)
            "templates": {
                "users_id",
                "users_id_orders",
            },  # GET /users/{id}, GET /users/{id}/orders
        }

        # The "tools" set in the server might contain more than our expected names
        # because all HTTP methods could be converted to tools - we just check for inclusion
        assert expected_names["tools"].issubset(server._used_names["tools"])
        assert expected_names["resources"].issubset(server._used_names["resources"])
        assert expected_names["templates"].issubset(server._used_names["templates"])

        # Check that the operationId is preferred for naming
        assert "listProducts" in server._used_names["resources"]

    @patch("fastmcp.server.openapi._combine_schemas")
    def test_custom_naming(self, mock_combine, simple_openapi_spec):
        """Test custom component naming function."""
        # Mock the HTTP client
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Mock the combine schemas function to return empty dict
        mock_combine.return_value = {}

        # Create a custom naming function
        def custom_namer(route, mcp_type, default_name):
            # Always prefix with component type
            if mcp_type == MCPType.TOOL:
                prefix = "tool"
            elif mcp_type == MCPType.RESOURCE:
                prefix = "res"
            elif mcp_type == MCPType.RESOURCE_TEMPLATE:
                prefix = "tmpl"
            else:
                prefix = "other"

            # Use operationId if available
            if route.operation_id:
                return f"{prefix}_{route.operation_id}"

            # Otherwise use the path
            path_name = route.path.replace("/", "_").replace("{", "").replace("}", "")
            return f"{prefix}{path_name}"

        # Create a custom testing server subclass
        class TestServer(FastMCPOpenAPI):
            def _create_openapi_tool(self, route, name):
                _tool_name = self._get_unique_name(name, "tools")
                # Don't actually create the tool, just record that the name was used

            def _create_openapi_resource(self, route, name):
                _resource_name = self._get_unique_name(name, "resources")
                # Don't actually create the resource, just record that the name was used

            def _create_openapi_template(self, route, name):
                _template_name = self._get_unique_name(name, "templates")
                # Don't actually create the template, just record that the name was used

        # Create a server with the custom naming
        server = TestServer(
            openapi_spec=simple_openapi_spec,
            client=mock_client,
            component_namer=custom_namer,
        )

        # Check some of the generated names
        assert "tool_users" in server._used_names["tools"]
        assert "res_users" in server._used_names["resources"]
        assert "tmpl_users_id" in server._used_names["templates"]
        assert "res_listProducts" in server._used_names["resources"]

    @patch("fastmcp.server.openapi._combine_schemas")
    def test_collision_handling(self, mock_combine, simple_openapi_spec):
        """Test how name collisions are handled by appending numbers."""
        # Mock the HTTP client
        mock_client = MagicMock(spec=httpx.AsyncClient)

        # Mock the combine schemas function to return empty dict
        mock_combine.return_value = {}

        # Create a custom naming function that always returns the same name
        def collision_namer(route, mcp_type, default_name):
            return "same_name"

        # Create a custom testing server subclass
        class TestServer(FastMCPOpenAPI):
            def _create_openapi_tool(self, route, name):
                _tool_name = self._get_unique_name(name, "tools")
                # Don't actually create the tool, just record that the name was used

            def _create_openapi_resource(self, route, name):
                _resource_name = self._get_unique_name(name, "resources")
                # Don't actually create the resource, just record that the name was used

            def _create_openapi_template(self, route, name):
                _template_name = self._get_unique_name(name, "templates")
                # Don't actually create the template, just record that the name was used

        # Create a server with the collision namer
        server = TestServer(
            openapi_spec=simple_openapi_spec,
            client=mock_client,
            component_namer=collision_namer,
        )

        # Check that names were renamed with numbers
        assert "same_name" in server._used_names["tools"]
        assert "same_name_2" in server._used_names["tools"]
        assert "same_name" in server._used_names["resources"]
        assert "same_name_2" in server._used_names["resources"]
        assert "same_name" in server._used_names["templates"]
        assert "same_name_2" in server._used_names["templates"]
