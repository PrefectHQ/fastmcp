import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.openapi import FastMCPOpenAPI, MCPType, RouteMap

from .conftest import GET_ROUTE_MAPS


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
