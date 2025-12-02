"""End-to-end compatibility tests between legacy and new OpenAPI implementations."""

import httpx
import pytest

from fastmcp.client import Client
from fastmcp.server.openapi import FastMCPOpenAPI
from fastmcp.server.openapi import FastMCPOpenAPI as LegacyFastMCPOpenAPI


class TestEndToEndCompatibility:
    """Test that legacy and new implementations create identical tools."""

    @pytest.fixture
    def simple_spec(self):
        """Simple OpenAPI spec for testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
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
                            },
                            {
                                "name": "include_details",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "boolean"},
                            },
                        ],
                        "responses": {"200": {"description": "User found"}},
                    }
                }
            },
        }

    @pytest.fixture
    def collision_spec(self):
        """OpenAPI spec with parameter collisions."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Collision API", "version": "1.0.0"},
            "paths": {
                "/users/{id}": {
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
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                        "required": ["name"],
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "User updated"}},
                    }
                }
            },
        }

    async def test_tool_schema_compatibility(self, simple_spec):
        """Test that tools have identical input schemas."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            # Create both servers
            legacy_server = LegacyFastMCPOpenAPI(
                openapi_spec=simple_spec,
                client=client,
                name="Legacy Server",
            )
            new_server = FastMCPOpenAPI(
                openapi_spec=simple_spec,
                client=client,
                name="New Server",
            )

            # Get tools from both servers
            async with Client(legacy_server) as legacy_client:
                legacy_tools = await legacy_client.list_tools()

            async with Client(new_server) as new_client:
                new_tools = await new_client.list_tools()

            # Should have same number of tools
            assert len(legacy_tools) == len(new_tools)
            assert len(legacy_tools) == 1

            # Get the single tool from each
            legacy_tool = legacy_tools[0]
            new_tool = new_tools[0]

            # Names should be identical
            assert legacy_tool.name == new_tool.name
            assert legacy_tool.name == "get_user"

            # Descriptions may differ (new server has simplified descriptions)
            # Just check that both have descriptions
            assert legacy_tool.description
            assert new_tool.description

            # Input schemas should be identical
            legacy_schema = legacy_tool.inputSchema
            new_schema = new_tool.inputSchema

            # Required fields should match
            assert set(legacy_schema.get("required", [])) == set(
                new_schema.get("required", [])
            )

            # Properties should match
            legacy_props = legacy_schema.get("properties", {})
            new_props = new_schema.get("properties", {})
            assert set(legacy_props.keys()) == set(new_props.keys())

            # Check each property
            for prop_name in legacy_props:
                legacy_prop = legacy_props[prop_name]
                new_prop = new_props[prop_name]

                # For required parameters, should have simple type
                if prop_name in legacy_schema.get("required", []):
                    assert legacy_prop.get("type") == new_prop.get("type")
                    assert "anyOf" not in legacy_prop
                    assert "anyOf" not in new_prop
                else:
                    # Both implementations now correctly preserve original schema without nullable behavior
                    assert "anyOf" not in legacy_prop
                    assert "anyOf" not in new_prop
                    # Both should have the same type
                    assert legacy_prop.get("type") == new_prop.get("type")

    async def test_collision_handling_compatibility(self, collision_spec):
        """Test that parameter collision handling is identical."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            # Create both servers
            legacy_server = LegacyFastMCPOpenAPI(
                openapi_spec=collision_spec,
                client=client,
                name="Legacy Server",
            )
            new_server = FastMCPOpenAPI(
                openapi_spec=collision_spec,
                client=client,
                name="New Server",
            )

            # Get tools from both servers
            async with Client(legacy_server) as legacy_client:
                legacy_tools = await legacy_client.list_tools()

            async with Client(new_server) as new_client:
                new_tools = await new_client.list_tools()

            # Should have same number of tools
            assert len(legacy_tools) == len(new_tools)
            assert len(legacy_tools) == 1

            # Get the single tool from each
            legacy_tool = legacy_tools[0]
            new_tool = new_tools[0]

            # Input schemas should be identical
            legacy_schema = legacy_tool.inputSchema
            new_schema = new_tool.inputSchema

            # Both should have collision-resolved parameters
            legacy_props = legacy_schema.get("properties", {})
            new_props = new_schema.get("properties", {})

            # Should have: id__path (path param), id (body param), name (body param)
            expected_props = {"id__path", "id", "name"}
            assert set(legacy_props.keys()) == expected_props
            assert set(new_props.keys()) == expected_props

            # Required should include path param and required body params
            legacy_required = set(legacy_schema.get("required", []))
            new_required = set(new_schema.get("required", []))
            assert legacy_required == new_required
            assert "id__path" in legacy_required
            assert "name" in legacy_required

            # Path parameter should have integer type
            assert legacy_props["id__path"]["type"] == "integer"
            assert new_props["id__path"]["type"] == "integer"

            # Body parameters should match
            assert legacy_props["id"]["type"] == "integer"
            assert new_props["id"]["type"] == "integer"
            assert legacy_props["name"]["type"] == "string"
            assert new_props["name"]["type"] == "string"

    async def test_tool_execution_parameter_mapping(self, collision_spec):
        """Test that tool execution with collisions works identically."""
        # This test verifies that both implementations can execute the same arguments
        # We can't easily test actual HTTP calls, but we can test argument validation

        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            # Create both servers
            legacy_server = LegacyFastMCPOpenAPI(
                openapi_spec=collision_spec,
                client=client,
                name="Legacy Server",
            )
            new_server = FastMCPOpenAPI(
                openapi_spec=collision_spec,
                client=client,
                name="New Server",
            )

            # Test arguments that should work with collision resolution
            test_args = {
                "id__path": 123,  # Path parameter (suffixed)
                "id": 456,  # Body parameter (not suffixed)
                "name": "John Doe",  # Body parameter
            }

            async with Client(legacy_server) as legacy_client:
                async with Client(new_server) as new_client:
                    # Both should accept the same arguments
                    # We'll test this by attempting to call the tools
                    # (they'll fail at HTTP level but should pass argument validation)

                    legacy_tools = await legacy_client.list_tools()
                    new_tools = await new_client.list_tools()

                    legacy_tool_name = legacy_tools[0].name
                    new_tool_name = new_tools[0].name

                    # Names should be identical
                    assert legacy_tool_name == new_tool_name

                    # Both should fail at the HTTP request level (not argument validation)
                    # This confirms the argument mapping works identically
                    with pytest.raises(Exception) as legacy_exc:
                        await legacy_client.call_tool(legacy_tool_name, test_args)

                    with pytest.raises(Exception) as new_exc:
                        await new_client.call_tool(new_tool_name, test_args)

                    # Both should fail with similar error types (HTTP-related, not schema validation)
                    # The exact error might differ but shouldn't be schema validation errors
                    legacy_error = str(legacy_exc.value)
                    new_error = str(new_exc.value)

                    # Neither should fail due to schema validation
                    assert "schema" not in legacy_error.lower()
                    assert "schema" not in new_error.lower()
                    assert "validation" not in legacy_error.lower()
                    assert "validation" not in new_error.lower()

    async def test_optional_parameter_handling(self, simple_spec):
        """Test that optional parameters are handled identically."""
        async with httpx.AsyncClient(base_url="https://api.example.com") as client:
            # Create both servers
            legacy_server = LegacyFastMCPOpenAPI(
                openapi_spec=simple_spec,
                client=client,
                name="Legacy Server",
            )
            new_server = FastMCPOpenAPI(
                openapi_spec=simple_spec,
                client=client,
                name="New Server",
            )

            # Test with optional parameter omitted (should be None/null)
            test_args_minimal = {"id": 123}

            # Test with optional parameter included
            test_args_full = {"id": 123, "include_details": True}

            async with Client(legacy_server) as legacy_client:
                async with Client(new_server) as new_client:
                    legacy_tools = await legacy_client.list_tools()
                    await new_client.list_tools()

                    tool_name = legacy_tools[0].name

                    # Both should handle minimal args the same way
                    with pytest.raises(Exception) as legacy_exc_min:
                        await legacy_client.call_tool(tool_name, test_args_minimal)

                    with pytest.raises(Exception) as new_exc_min:
                        await new_client.call_tool(tool_name, test_args_minimal)

                    # Both should handle full args the same way
                    with pytest.raises(Exception) as legacy_exc_full:
                        await legacy_client.call_tool(tool_name, test_args_full)

                    with pytest.raises(Exception) as new_exc_full:
                        await new_client.call_tool(tool_name, test_args_full)

                    # All should fail at HTTP level, not schema validation
                    for exc in [
                        legacy_exc_min,
                        new_exc_min,
                        legacy_exc_full,
                        new_exc_full,
                    ]:
                        error_msg = str(exc.value).lower()
                        assert "schema" not in error_msg
                        assert "validation" not in error_msg
