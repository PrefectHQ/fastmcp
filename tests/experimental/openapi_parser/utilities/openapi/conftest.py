"""Shared fixtures for openapi_new utilities tests."""

import pytest


@pytest.fixture
def basic_openapi_30_spec():
    """Basic OpenAPI 3.0 spec for testing."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
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
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "User retrieved successfully",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


@pytest.fixture
def basic_openapi_31_spec():
    """Basic OpenAPI 3.1 spec for testing."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
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
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "User retrieved successfully",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


@pytest.fixture
def collision_spec():
    """OpenAPI spec with parameter name collisions."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Collision Test API", "version": "1.0.0"},
        "paths": {
            "/users/{id}": {
                "put": {
                    "operationId": "update_user",
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
                    "responses": {"200": {"description": "Updated"}},
                }
            }
        },
    }


@pytest.fixture
def deepobject_spec():
    """OpenAPI spec with deepObject parameter style."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "DeepObject Test API", "version": "1.0.0"},
        "paths": {
            "/search": {
                "get": {
                    "operationId": "search",
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "required": False,
                            "style": "deepObject",
                            "explode": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string"},
                                    "price": {
                                        "type": "object",
                                        "properties": {
                                            "min": {"type": "number"},
                                            "max": {"type": "number"},
                                        },
                                    },
                                },
                            },
                        }
                    ],
                    "responses": {"200": {"description": "Search results"}},
                }
            }
        },
    }


@pytest.fixture
def complex_spec():
    """Complex OpenAPI spec with multiple parameter types."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Complex API", "version": "1.0.0"},
        "paths": {
            "/items/{id}": {
                "patch": {
                    "operationId": "update_item",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "version",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "default": 1},
                        },
                        {
                            "name": "X-Client-Version",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "description": {"type": "string"},
                                        "tags": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": ["title"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Item updated"}},
                }
            }
        },
    }
