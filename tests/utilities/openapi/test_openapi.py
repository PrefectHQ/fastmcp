"""Tests for the OpenAPI parsing utilities."""

from typing import Any

import pytest
from fastapi import Body, FastAPI, Path, Query
from pydantic import BaseModel, Field

from fastmcp.utilities.openapi import parse_openapi_to_http_routes

# --- Test Data: Static OpenAPI Schema Dictionaries --- #


@pytest.fixture
def petstore_schema() -> dict[str, Any]:
    """Fixture that returns a simple Pet Store API schema."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Simple Pet Store API", "version": "1.0.0"},
        "paths": {
            "/pets": {
                "get": {
                    "summary": "List all pets",
                    "operationId": "listPets",
                    "tags": ["pets"],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "How many items to return",
                            "required": False,
                            "schema": {"type": "integer", "format": "int32"},
                        }
                    ],
                    "responses": {"200": {"description": "A paged array of pets"}},
                },
                "post": {
                    "summary": "Create a pet",
                    "operationId": "createPet",
                    "tags": ["pets"],
                    "requestBody": {"$ref": "#/components/requestBodies/PetBody"},
                    "responses": {"201": {"description": "Null response"}},
                },
            },
            "/pets/{petId}": {
                "get": {
                    "summary": "Info for a specific pet",
                    "operationId": "showPetById",
                    "tags": ["pets"],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "description": "The id of the pet",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "X-Request-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string", "format": "uuid"},
                        },
                    ],
                    "responses": {"200": {"description": "Information about the pet"}},
                },
                "parameters": [  # Path level parameter example
                    {
                        "name": "traceId",
                        "in": "header",
                        "description": "Common trace ID",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                ],
            },
        },
        "components": {
            "schemas": {
                "Pet": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "integer", "format": "int64"},
                        "name": {"type": "string"},
                        "tag": {"type": "string"},
                    },
                }
            },
            "requestBodies": {
                "PetBody": {
                    "description": "Pet object",
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Pet"}
                        }
                    },
                }
            },
        },
    }


@pytest.fixture
def parsed_petstore_routes(petstore_schema):
    """Return parsed routes from the PetStore schema."""
    return parse_openapi_to_http_routes(petstore_schema)


@pytest.fixture
def bookstore_schema() -> dict[str, Any]:
    """Fixture that returns a Book Store API schema with different parameter types."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Book Store API", "version": "1.0.0"},
        "paths": {
            "/books": {
                "get": {
                    "summary": "List all books",
                    "operationId": "listBooks",
                    "tags": ["books"],
                    "parameters": [
                        {
                            "name": "genre",
                            "in": "query",
                            "description": "Filter by genre",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "published_after",
                            "in": "query",
                            "description": "Filter by publication date",
                            "required": False,
                            "schema": {"type": "string", "format": "date"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Maximum number of results",
                            "required": False,
                            "schema": {"type": "integer", "default": 10},
                        },
                    ],
                    "responses": {"200": {"description": "A list of books"}},
                },
                "post": {
                    "summary": "Create a new book",
                    "operationId": "createBook",
                    "tags": ["books"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["title", "author"],
                                    "properties": {
                                        "title": {"type": "string"},
                                        "author": {"type": "string"},
                                        "isbn": {"type": "string"},
                                        "published": {
                                            "type": "string",
                                            "format": "date",
                                        },
                                        "genre": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Book created"}},
                },
            },
            "/books/{isbn}": {
                "get": {
                    "summary": "Get book by ISBN",
                    "operationId": "getBook",
                    "tags": ["books"],
                    "parameters": [
                        {
                            "name": "isbn",
                            "in": "path",
                            "required": True,
                            "description": "ISBN of the book",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Book details"}},
                },
                "delete": {
                    "summary": "Delete a book",
                    "operationId": "deleteBook",
                    "tags": ["books"],
                    "parameters": [
                        {
                            "name": "isbn",
                            "in": "path",
                            "required": True,
                            "description": "ISBN of the book to delete",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"204": {"description": "Book deleted"}},
                },
            },
        },
    }


@pytest.fixture
def parsed_bookstore_routes(bookstore_schema):
    """Return parsed routes from the BookStore schema."""
    return parse_openapi_to_http_routes(bookstore_schema)


# --- FastAPI App Fixtures --- #


class Item(BaseModel):
    """Example pydantic model for API testing."""

    name: str
    description: str | None = None
    price: float
    tax: float | None = None
    tags: list[str] = Field(default_factory=list)


@pytest.fixture
def fastapi_app() -> FastAPI:
    """Fixture that returns a FastAPI app with various types of endpoints."""
    app = FastAPI(title="Test API", version="1.0.0")

    @app.get("/items/", operation_id="list_items")
    async def list_items(skip: int = 0, limit: int = 10):
        """List all items with pagination."""
        return [
            {"name": f"Item {i}", "price": float(i)} for i in range(skip, skip + limit)
        ]

    @app.post("/items/", operation_id="create_item")
    async def create_item(item: Item):
        """Create a new item."""
        return item

    @app.get("/items/{item_id}", operation_id="get_item")
    async def get_item(
        item_id: int = Path(..., description="The ID of the item to get"),
        q: str | None = Query(None, description="Optional query string"),
    ):
        """Get an item by ID."""
        return {"item_id": item_id, "q": q}

    @app.put("/items/{item_id}", operation_id="update_item")
    async def update_item(
        item_id: int = Path(..., description="The ID of the item to update"),
        item: Item = Body(..., description="The updated item data"),
    ):
        """Update an existing item."""
        return {"item_id": item_id, **item.model_dump()}

    @app.delete("/items/{item_id}", operation_id="delete_item")
    async def delete_item(
        item_id: int = Path(..., description="The ID of the item to delete"),
    ):
        """Delete an item by ID."""
        return {"item_id": item_id, "deleted": True}

    @app.get("/items/{item_id}/tags/{tag_id}", operation_id="get_item_tag")
    async def get_item_tag(
        item_id: int = Path(..., description="The ID of the item"),
        tag_id: str = Path(..., description="The ID of the tag"),
    ):
        """Get a specific tag for an item."""
        return {"item_id": item_id, "tag_id": tag_id}

    @app.post("/upload/", operation_id="upload_file")
    async def upload_file(
        file_name: str = Query(..., description="Name of the file to upload"),
        content_type: str = Query(..., description="Content type of the file"),
    ):
        """Upload a file (dummy endpoint for testing query params with POST)."""
        return {
            "file_name": file_name,
            "content_type": content_type,
            "status": "uploaded",
        }

    return app


@pytest.fixture
def fastapi_openapi_schema(fastapi_app) -> dict[str, Any]:
    """Fixture that returns the OpenAPI schema of the FastAPI app."""
    return fastapi_app.openapi()


@pytest.fixture
def parsed_fastapi_routes(fastapi_openapi_schema):
    """Return parsed routes from a FastAPI OpenAPI schema."""
    return parse_openapi_to_http_routes(fastapi_openapi_schema)


@pytest.fixture
def fastapi_route_map(parsed_fastapi_routes):
    """Return a dictionary of routes by operation ID."""
    return {
        r.operation_id: r for r in parsed_fastapi_routes if r.operation_id is not None
    }


# --- Tests for PetStore schema --- #


def test_petstore_route_count(parsed_petstore_routes):
    """Test that parsing the PetStore schema correctly identifies the number of routes."""
    assert len(parsed_petstore_routes) == 3


def test_petstore_get_pets_operation_id(parsed_petstore_routes):
    """Test that GET /pets operation_id is correctly parsed."""
    get_pets = next(
        (r for r in parsed_petstore_routes if r.method == "GET" and r.path == "/pets"),
        None,
    )
    assert get_pets is not None
    assert get_pets.operation_id == "listPets"


def test_petstore_query_parameter(parsed_petstore_routes):
    """Test that query parameter 'limit' is correctly parsed from the schema."""
    get_pets = next(
        (r for r in parsed_petstore_routes if r.method == "GET" and r.path == "/pets"),
        None,
    )

    assert get_pets is not None
    assert len(get_pets.parameters) == 1
    param = get_pets.parameters[0]
    assert param.name == "limit"
    assert param.location == "query"
    assert param.required is False
    assert param.schema_.get("type") == "integer"
    assert param.schema_.get("format") == "int32"


def test_petstore_path_parameter(parsed_petstore_routes):
    """Test that path parameter 'petId' is correctly parsed from the schema."""
    get_pet = next(
        (
            r
            for r in parsed_petstore_routes
            if r.method == "GET" and r.path == "/pets/{petId}"
        ),
        None,
    )

    assert get_pet is not None
    path_param = next((p for p in get_pet.parameters if p.name == "petId"), None)
    assert path_param is not None
    assert path_param.location == "path"
    assert path_param.required is True
    assert path_param.schema_.get("type") == "string"


def test_petstore_header_parameters(parsed_petstore_routes):
    """Test that header parameters are correctly parsed from the schema."""
    get_pet = next(
        (
            r
            for r in parsed_petstore_routes
            if r.method == "GET" and r.path == "/pets/{petId}"
        ),
        None,
    )

    assert get_pet is not None
    header_params = [p for p in get_pet.parameters if p.location == "header"]
    assert len(header_params) == 2


def test_petstore_header_parameter_names(parsed_petstore_routes):
    """Test that header parameter names are correctly parsed."""
    get_pet = next(
        (
            r
            for r in parsed_petstore_routes
            if r.method == "GET" and r.path == "/pets/{petId}"
        ),
        None,
    )

    assert get_pet is not None
    header_params = [p for p in get_pet.parameters if p.location == "header"]
    header_names = [p.name for p in header_params]
    assert "X-Request-ID" in header_names
    assert "traceId" in header_names


def test_petstore_path_level_parameters(parsed_petstore_routes):
    """Test that path-level parameters are correctly merged into the operation."""
    get_pet = next(
        (
            r
            for r in parsed_petstore_routes
            if r.method == "GET" and r.path == "/pets/{petId}"
        ),
        None,
    )

    assert get_pet is not None
    trace_param = next((p for p in get_pet.parameters if p.name == "traceId"), None)
    assert trace_param is not None
    assert trace_param.location == "header"
    assert trace_param.required is False


def test_petstore_request_body_reference_resolution(parsed_petstore_routes):
    """Test that request body references are correctly resolved."""
    create_pet = next(
        (r for r in parsed_petstore_routes if r.method == "POST" and r.path == "/pets"),
        None,
    )

    assert create_pet is not None
    assert create_pet.request_body is not None
    assert create_pet.request_body.required is True
    assert "application/json" in create_pet.request_body.content_schema


def test_petstore_schema_reference_resolution(parsed_petstore_routes):
    """Test that schema references in request bodies are correctly resolved."""
    create_pet = next(
        (r for r in parsed_petstore_routes if r.method == "POST" and r.path == "/pets"),
        None,
    )

    assert create_pet is not None
    assert create_pet.request_body is not None
    json_schema = create_pet.request_body.content_schema["application/json"]
    properties = json_schema.get("properties", {})

    assert "id" in properties
    assert "name" in properties
    assert "tag" in properties


def test_petstore_required_fields_resolution(parsed_petstore_routes):
    """Test that required fields are correctly resolved from referenced schemas."""
    create_pet = next(
        (r for r in parsed_petstore_routes if r.method == "POST" and r.path == "/pets"),
        None,
    )

    assert create_pet is not None
    assert create_pet.request_body is not None
    json_schema = create_pet.request_body.content_schema["application/json"]
    assert json_schema.get("required") == ["id", "name"]


# --- Tests for BookStore schema --- #


def test_bookstore_route_count(parsed_bookstore_routes):
    """Test that parsing the BookStore schema correctly identifies the number of routes."""
    assert len(parsed_bookstore_routes) == 4


def test_bookstore_query_parameter_count(parsed_bookstore_routes):
    """Test that the correct number of query parameters are parsed."""
    list_books = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "listBooks"), None
    )

    assert list_books is not None
    assert len(list_books.parameters) == 3


def test_bookstore_query_parameter_names(parsed_bookstore_routes):
    """Test that query parameter names are correctly parsed."""
    list_books = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "listBooks"), None
    )

    assert list_books is not None
    param_map = {p.name: p for p in list_books.parameters}
    assert "genre" in param_map
    assert "published_after" in param_map
    assert "limit" in param_map


def test_bookstore_query_parameter_formats(parsed_bookstore_routes):
    """Test that query parameter formats are correctly parsed."""
    list_books = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "listBooks"), None
    )

    assert list_books is not None
    param_map = {p.name: p for p in list_books.parameters}
    assert param_map["published_after"].schema_.get("format") == "date"


def test_bookstore_query_parameter_defaults(parsed_bookstore_routes):
    """Test that query parameter default values are correctly parsed."""
    list_books = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "listBooks"), None
    )

    assert list_books is not None
    param_map = {p.name: p for p in list_books.parameters}
    assert param_map["limit"].schema_.get("default") == 10


def test_bookstore_inline_request_body_presence(parsed_bookstore_routes):
    """Test that request bodies with inline schemas are present."""
    create_book = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "createBook"), None
    )

    assert create_book is not None
    assert create_book.request_body is not None
    assert create_book.request_body.required is True
    assert "application/json" in create_book.request_body.content_schema


def test_bookstore_inline_request_body_properties(parsed_bookstore_routes):
    """Test that request body properties are correctly parsed from inline schemas."""
    create_book = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "createBook"), None
    )

    assert create_book is not None
    assert create_book.request_body is not None

    json_schema = create_book.request_body.content_schema["application/json"]
    properties = json_schema.get("properties", {})

    assert "title" in properties
    assert "author" in properties
    assert "isbn" in properties
    assert "published" in properties
    assert "genre" in properties


def test_bookstore_inline_request_body_required_fields(parsed_bookstore_routes):
    """Test that required fields in inline schema are correctly parsed."""
    create_book = next(
        (r for r in parsed_bookstore_routes if r.operation_id == "createBook"), None
    )

    assert create_book is not None
    assert create_book.request_body is not None

    json_schema = create_book.request_body.content_schema["application/json"]
    assert json_schema.get("required") == ["title", "author"]


def test_bookstore_delete_method(parsed_bookstore_routes):
    """Test that DELETE method is correctly parsed from the schema."""
    delete_book = next(
        (r for r in parsed_bookstore_routes if r.method == "DELETE"), None
    )

    assert delete_book is not None
    assert delete_book.operation_id == "deleteBook"
    assert delete_book.path == "/books/{isbn}"


def test_bookstore_delete_method_parameters(parsed_bookstore_routes):
    """Test that parameters for DELETE method are correctly parsed."""
    delete_book = next(
        (r for r in parsed_bookstore_routes if r.method == "DELETE"), None
    )

    assert delete_book is not None
    assert len(delete_book.parameters) == 1
    assert delete_book.parameters[0].name == "isbn"


# --- Tests for FastAPI Generated Schema --- #


def test_fastapi_route_count(parsed_fastapi_routes):
    """Test that parsing a FastAPI-generated schema correctly identifies the number of routes."""
    assert len(parsed_fastapi_routes) == 7


def test_fastapi_parameter_default_values(fastapi_route_map):
    """Test that default parameter values are correctly parsed from the schema."""
    list_items = fastapi_route_map["list_items"]

    param_map = {p.name: p for p in list_items.parameters}
    assert "skip" in param_map
    assert "limit" in param_map


def test_fastapi_skip_parameter_default(fastapi_route_map):
    """Test that skip parameter default value is correctly parsed."""
    list_items = fastapi_route_map["list_items"]

    param_map = {p.name: p for p in list_items.parameters}
    assert param_map["skip"].schema_.get("default") == 0


def test_fastapi_limit_parameter_default(fastapi_route_map):
    """Test that limit parameter default value is correctly parsed."""
    list_items = fastapi_route_map["list_items"]

    param_map = {p.name: p for p in list_items.parameters}
    assert param_map["limit"].schema_.get("default") == 10


def test_fastapi_request_body_from_pydantic(fastapi_route_map):
    """Test that request bodies from Pydantic models are present."""
    create_item = fastapi_route_map["create_item"]

    assert create_item.request_body is not None
    assert "application/json" in create_item.request_body.content_schema


def test_fastapi_request_body_properties(fastapi_route_map):
    """Test that request body properties from Pydantic models are correctly parsed."""
    create_item = fastapi_route_map["create_item"]

    json_schema = create_item.request_body.content_schema["application/json"]
    properties = json_schema.get("properties", {})

    assert "name" in properties
    assert "description" in properties
    assert "price" in properties
    assert "tax" in properties
    assert "tags" in properties


def test_fastapi_request_body_required_fields(fastapi_route_map):
    """Test that required fields from Pydantic models are correctly parsed."""
    create_item = fastapi_route_map["create_item"]

    json_schema = create_item.request_body.content_schema["application/json"]
    required = json_schema.get("required", [])

    assert "name" in required
    assert "price" in required


def test_fastapi_path_parameter_presence(fastapi_route_map):
    """Test that path parameters are present in FastAPI schema."""
    get_item = fastapi_route_map["get_item"]

    path_params = [p for p in get_item.parameters if p.location == "path"]
    assert len(path_params) == 1


def test_fastapi_path_parameter_properties(fastapi_route_map):
    """Test that path parameters properties are correctly parsed."""
    get_item = fastapi_route_map["get_item"]

    path_params = [p for p in get_item.parameters if p.location == "path"]
    assert path_params[0].name == "item_id"
    assert path_params[0].required is True


def test_fastapi_optional_query_parameter(fastapi_route_map):
    """Test that optional query parameters are correctly parsed."""
    get_item = fastapi_route_map["get_item"]

    query_params = [p for p in get_item.parameters if p.location == "query"]
    assert len(query_params) == 1
    assert query_params[0].name == "q"
    assert query_params[0].required is False


def test_fastapi_multiple_path_parameter_count(fastapi_route_map):
    """Test that multiple path parameters count is correct."""
    get_item_tag = fastapi_route_map["get_item_tag"]

    path_params = [p for p in get_item_tag.parameters if p.location == "path"]
    assert len(path_params) == 2


def test_fastapi_multiple_path_parameter_names(fastapi_route_map):
    """Test that multiple path parameter names are correctly parsed."""
    get_item_tag = fastapi_route_map["get_item_tag"]

    path_params = [p for p in get_item_tag.parameters if p.location == "path"]
    param_names = [p.name for p in path_params]
    assert "item_id" in param_names
    assert "tag_id" in param_names


def test_fastapi_post_with_query_parameters(fastapi_route_map):
    """Test that query parameters for POST methods are correctly parsed."""
    upload_file = fastapi_route_map["upload_file"]

    assert upload_file.method == "POST"
    query_params = [p for p in upload_file.parameters if p.location == "query"]
    assert len(query_params) == 2


def test_fastapi_post_query_parameter_names(fastapi_route_map):
    """Test that query parameter names for POST methods are correctly parsed."""
    upload_file = fastapi_route_map["upload_file"]

    query_params = [p for p in upload_file.parameters if p.location == "query"]
    param_names = [p.name for p in query_params]
    assert "file_name" in param_names
    assert "content_type" in param_names
