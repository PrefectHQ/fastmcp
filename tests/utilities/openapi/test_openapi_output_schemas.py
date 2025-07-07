"""Tests for OpenAPI output schema extraction functionality."""

from fastmcp.utilities.openapi import (
    ResponseInfo,
    extract_output_schema_from_responses,
)


class TestExtractOutputSchema:
    """Test the extract_output_schema_from_responses function."""

    def test_extract_object_schema(self):
        """Test extracting object output schema (no wrapping needed)."""
        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                        "required": ["id", "name"],
                    }
                },
            )
        }

        result = extract_output_schema_from_responses(responses)

        assert result == {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
            "required": ["id", "name"],
        }
        assert result is not None and "x-fastmcp-wrap-result" not in result

    def test_extract_array_schema_with_wrapping(self):
        """Test extracting array output schema (should be wrapped)."""
        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                            },
                        },
                    }
                },
            )
        }

        result = extract_output_schema_from_responses(responses)

        assert result == {
            "type": "object",
            "properties": {
                "result": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                }
            },
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }

    def test_extract_primitive_schema_with_wrapping(self):
        """Test extracting primitive output schema (should be wrapped)."""
        responses = {
            "201": ResponseInfo(
                description="Created",
                content_schema={
                    "application/json": {
                        "type": "string",
                        "description": "ID of created resource",
                    }
                },
            )
        }

        result = extract_output_schema_from_responses(responses)

        assert result == {
            "type": "object",
            "properties": {
                "result": {"type": "string", "description": "ID of created resource"}
            },
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }

    def test_priority_of_success_codes(self):
        """Test that 200 takes priority over other success codes."""
        responses = {
            "201": ResponseInfo(
                description="Created",
                content_schema={"application/json": {"type": "string"}},
            ),
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    }
                },
            ),
        }

        result = extract_output_schema_from_responses(responses)

        # Should use the 200 response (object), not 201 (string)
        assert result is not None and result["type"] == "object"
        assert result is not None and "x-fastmcp-wrap-result" not in result

    def test_prefer_json_content_type(self):
        """Test that application/json is preferred over other content types."""
        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "text/plain": {"type": "string"},
                    "application/json": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            )
        }

        result = extract_output_schema_from_responses(responses)

        # Should use the application/json schema (object), not text/plain (string)
        assert result is not None and result["type"] == "object"
        assert result is not None and "x-fastmcp-wrap-result" not in result

    def test_no_responses(self):
        """Test that None is returned when no responses are provided."""
        result = extract_output_schema_from_responses({})
        assert result is None

    def test_no_success_responses(self):
        """Test that None is returned when no success responses are found."""
        responses = {
            "400": ResponseInfo(
                description="Bad Request",
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                    }
                },
            )
        }

        result = extract_output_schema_from_responses(responses)
        assert result is None

    def test_no_content_schema(self):
        """Test that None is returned when response has no content schema."""
        responses = {"204": ResponseInfo(description="No Content")}

        result = extract_output_schema_from_responses(responses)
        assert result is None

    def test_schema_definitions_included(self):
        """Test that schema definitions are properly included in output schema."""
        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {"user": {"$ref": "#/$defs/User"}},
                    }
                },
            )
        }

        schema_definitions = {
            "User": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                "required": ["id", "name"],
            }
        }

        result = extract_output_schema_from_responses(responses, schema_definitions)

        assert result is not None
        assert "$defs" in result
        assert "User" in result["$defs"]
        assert result["$defs"]["User"] == schema_definitions["User"]

    def test_wrapped_schema_with_definitions(self):
        """Test that wrapped schemas properly include schema definitions."""
        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/User"},
                    }
                },
            )
        }

        schema_definitions = {
            "User": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                "required": ["id", "name"],
            }
        }

        result = extract_output_schema_from_responses(responses, schema_definitions)

        assert result is not None
        assert result["x-fastmcp-wrap-result"] is True
        assert "$defs" in result
        assert "User" in result["$defs"]
        assert result["properties"]["result"]["type"] == "array"
        assert result["properties"]["result"]["items"]["$ref"] == "#/$defs/User"
