"""Tests for allOf handling at requestBody top level."""

from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    RequestBodyInfo,
)
from fastmcp.utilities.openapi.schemas import _combine_schemas


def test_allof_at_requestbody_top_level():
    """Test that allOf schemas at requestBody top level are properly merged."""

    # Create a route with allOf at the requestBody top level
    route = HTTPRoute(
        path="/test",
        method="POST",
        operation_id="testOperation",
        parameters=[],
        request_body=RequestBodyInfo(
            required=True,
            content_schema={
                "application/json": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "age": {"type": "integer"},
                            },
                            "required": ["name"],
                        },
                        {
                            "type": "object",
                            "properties": {
                                "email": {"type": "string"},
                                "phone": {"type": "string"},
                            },
                            "required": ["email"],
                        },
                    ]
                }
            },
        ),
        responses={},
    )

    # Combine schemas - this should merge allOf schemas
    combined = _combine_schemas(route)

    # Check that all properties from both allOf schemas are present
    properties = combined.get("properties", {})
    assert "name" in properties
    assert "age" in properties
    assert "email" in properties
    assert "phone" in properties

    # Check property types
    assert properties["name"]["type"] == "string"
    assert properties["age"]["type"] == "integer"
    assert properties["email"]["type"] == "string"
    assert properties["phone"]["type"] == "string"

    # Check that required fields are merged correctly
    required = set(combined.get("required", []))
    assert "name" in required
    assert "email" in required

    # allOf should be removed after merging
    assert "allOf" not in combined


def test_allof_with_nested_properties():
    """Test allOf with nested object properties."""

    route = HTTPRoute(
        path="/test",
        method="POST",
        operation_id="testNested",
        parameters=[],
        request_body=RequestBodyInfo(
            required=True,
            content_schema={
                "application/json": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {
                                "user": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                    },
                                }
                            },
                            "required": ["user"],
                        },
                        {
                            "type": "object",
                            "properties": {
                                "metadata": {
                                    "type": "object",
                                    "properties": {
                                        "created": {"type": "string"},
                                        "updated": {"type": "string"},
                                    },
                                }
                            },
                        },
                    ]
                }
            },
        ),
        responses={},
    )

    combined = _combine_schemas(route)

    # Check nested properties are preserved
    properties = combined.get("properties", {})
    assert "user" in properties
    assert "metadata" in properties

    # Check nested structure
    assert properties["user"]["type"] == "object"
    assert "id" in properties["user"]["properties"]
    assert "name" in properties["user"]["properties"]

    assert properties["metadata"]["type"] == "object"
    assert "created" in properties["metadata"]["properties"]
    assert "updated" in properties["metadata"]["properties"]

    # Check required
    required = set(combined.get("required", []))
    assert "user" in required
    assert "metadata" not in required  # Not in any required array


def test_allof_with_overlapping_properties():
    """Test allOf with overlapping property names (later schemas override)."""

    route = HTTPRoute(
        path="/test",
        method="POST",
        operation_id="testOverlap",
        parameters=[],
        request_body=RequestBodyInfo(
            required=True,
            content_schema={
                "application/json": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "age": {"type": "integer"},
                            },
                            "required": ["name"],
                        },
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "maxLength": 50},  # Override
                                "email": {"type": "string"},
                            },
                            "required": ["email"],
                        },
                    ]
                }
            },
        ),
        responses={},
    )

    combined = _combine_schemas(route)

    properties = combined.get("properties", {})

    # Later schema should win for overlapping properties
    assert "name" in properties
    assert properties["name"]["type"] == "string"
    assert "maxLength" in properties["name"]  # From second schema
    assert properties["name"]["maxLength"] == 50

    # Check other properties
    assert "age" in properties
    assert "email" in properties

    # Both name and email should be required
    required = set(combined.get("required", []))
    assert "name" in required
    assert "email" in required


def test_no_allof_passthrough():
    """Test that schemas without allOf pass through unchanged."""

    route = HTTPRoute(
        path="/test",
        method="POST",
        operation_id="testNoAllOf",
        parameters=[],
        request_body=RequestBodyInfo(
            required=True,
            content_schema={
                "application/json": {
                    "type": "object",
                    "properties": {"simple": {"type": "string"}},
                    "required": ["simple"],
                }
            },
        ),
        responses={},
    )

    combined = _combine_schemas(route)

    # Should pass through unchanged
    properties = combined.get("properties", {})
    assert "simple" in properties
    assert properties["simple"]["type"] == "string"

    required = set(combined.get("required", []))
    assert "simple" in required

    # No allOf in original or result
    assert "allOf" not in combined
