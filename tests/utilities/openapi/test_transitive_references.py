"""Comprehensive tests for transitive and nested reference handling (Issue #1372)."""

from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
    ResponseInfo,
)
from fastmcp.utilities.openapi.parser import parse_openapi_to_http_routes
from fastmcp.utilities.openapi.schemas import (
    _combine_schemas_and_map_params,
    extract_output_schema_from_responses,
)


class TestTransitiveAndNestedReferences:
    """Comprehensive tests for transitive and nested reference handling (Issue #1372)."""

    def test_nested_refs_in_schema_definitions_converted(self):
        """$refs inside schema definitions must be converted from OpenAPI to JSON Schema format."""
        route = HTTPRoute(
            path="/users/{id}",
            method="POST",
            operation_id="create_user",
            parameters=[
                ParameterInfo(
                    name="id", location="path", required=True, schema={"type": "string"}
                )
            ],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {"user": {"$ref": "#/components/schemas/User"}},
                    }
                },
            ),
            request_schemas={
                "User": {
                    "type": "object",
                    "properties": {"profile": {"$ref": "#/components/schemas/Profile"}},
                },
                "Profile": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # Root level refs should be converted
        assert combined_schema["properties"]["user"]["$ref"] == "#/$defs/User"

        # Refs inside schema definitions should also be converted
        user_def = combined_schema["$defs"]["User"]
        assert user_def["properties"]["profile"]["$ref"] == "#/$defs/Profile"

    def test_transitive_dependencies_in_response_schemas(self):
        """Transitive dependencies (A→B→C) must all be preserved in response schemas."""
        # This mimics the exact structure reported in issue #1372
        responses = {
            "201": ResponseInfo(
                description="User created",
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/User"}
                },
            )
        }

        schema_definitions = {
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "profile": {"$ref": "#/components/schemas/Profile"},
                },
                "required": ["id", "profile"],
            },
            "Profile": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"$ref": "#/components/schemas/Address"},
                },
                "required": ["name", "address"],
            },
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                    "zipcode": {"type": "string"},
                },
                "required": ["street", "city", "zipcode"],
            },
        }

        result = extract_output_schema_from_responses(
            responses, schema_definitions=schema_definitions, openapi_version="3.0.3"
        )

        # All transitive dependencies must be preserved
        assert result is not None
        assert "$defs" in result
        assert "User" in result["$defs"], "User should be preserved"
        assert "Profile" in result["$defs"], "Profile should be preserved"
        assert "Address" in result["$defs"], "Address must be preserved (main bug)"

        # All refs should be converted to #/$defs format
        user_def = result["$defs"]["User"]
        assert user_def["properties"]["profile"]["$ref"] == "#/$defs/Profile"

        profile_def = result["$defs"]["Profile"]
        assert profile_def["properties"]["address"]["$ref"] == "#/$defs/Address"

    def test_elongl_reported_case_xref_with_nullable_function(self):
        """Test the specific case reported by elongl with nullable function reference."""
        responses = {
            "200": ResponseInfo(
                description="Success",
                content_schema={
                    "application/json": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Xref"},
                    }
                },
            )
        }

        schema_definitions = {
            "Xref": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "title": "Address"},
                    "type": {"type": "string", "title": "Type"},
                    "function": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/Function"},
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["address", "type", "function"],
                "title": "Xref",
            },
            "Function": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                },
                "title": "Function",
            },
        }

        result = extract_output_schema_from_responses(
            responses, schema_definitions=schema_definitions
        )

        # Function must be included in $defs
        assert result is not None
        assert "$defs" in result
        assert "Xref" in result["$defs"], "Xref should be preserved"
        assert "Function" in result["$defs"], (
            "Function must be preserved (reported bug)"
        )

        # Refs in anyOf should be converted
        xref_def = result["$defs"]["Xref"]
        function_prop = xref_def["properties"]["function"]
        assert function_prop["anyOf"][0]["$ref"] == "#/$defs/Function"

    def test_tspicer_reported_case_profile_with_nested_refs(self):
        """Test the specific case reported by tspicer with Profile->countryCode->AccountInfo."""
        route = HTTPRoute(
            path="/profile",
            method="POST",
            operation_id="create_profile",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/Profile"}
                },
            ),
            request_schemas={
                "Profile": {
                    "type": "object",
                    "properties": {
                        "profileId": {"type": "integer"},
                        "countryCode": {"$ref": "#/components/schemas/countryCode"},
                        "accountInfo": {"$ref": "#/components/schemas/AccountInfo"},
                    },
                },
                "countryCode": {
                    "type": "string",
                    "enum": ["US", "UK", "CA", "AU"],
                },
                "AccountInfo": {
                    "type": "object",
                    "properties": {
                        "accountId": {"type": "string"},
                        "accountType": {"type": "string"},
                    },
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # All referenced schemas must be included in $defs
        assert "Profile" in combined_schema["$defs"], "Profile should be preserved"
        assert "countryCode" in combined_schema["$defs"], (
            "countryCode must be preserved"
        )
        assert "AccountInfo" in combined_schema["$defs"], (
            "AccountInfo must be preserved"
        )

        # All refs should be converted
        profile_def = combined_schema["$defs"]["Profile"]
        assert profile_def["properties"]["countryCode"]["$ref"] == "#/$defs/countryCode"
        assert profile_def["properties"]["accountInfo"]["$ref"] == "#/$defs/AccountInfo"

    def test_transitive_refs_in_request_body_schemas(self):
        """Transitive $refs in request body schemas must be preserved and converted."""
        route = HTTPRoute(
            path="/users",
            method="POST",
            operation_id="create_user",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/User"}
                },
            ),
            request_schemas={
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "profile": {"$ref": "#/components/schemas/Profile"},
                    },
                    "required": ["id", "profile"],
                },
                "Profile": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "address": {"$ref": "#/components/schemas/Address"},
                    },
                    "required": ["name", "address"],
                },
                "Address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                        "zipcode": {"type": "string"},
                    },
                    "required": ["street", "city", "zipcode"],
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # All transitive dependencies should be preserved
        assert "User" in combined_schema["$defs"]
        assert "Profile" in combined_schema["$defs"]
        assert "Address" in combined_schema["$defs"]

        # All internal refs should be converted to #/$defs format
        user_def = combined_schema["$defs"]["User"]
        assert user_def["properties"]["profile"]["$ref"] == "#/$defs/Profile"

        profile_def = combined_schema["$defs"]["Profile"]
        assert profile_def["properties"]["address"]["$ref"] == "#/$defs/Address"

    def test_refs_in_array_items_converted(self):
        """$refs inside array items must be converted from OpenAPI to JSON Schema format."""
        route = HTTPRoute(
            path="/users",
            method="POST",
            operation_id="create_users",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "users": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/User"},
                            }
                        },
                    }
                },
            ),
            request_schemas={
                "User": {
                    "type": "object",
                    "properties": {"profile": {"$ref": "#/components/schemas/Profile"}},
                },
                "Profile": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # Array item refs should be converted
        assert combined_schema["properties"]["users"]["items"]["$ref"] == "#/$defs/User"

        # Nested refs should be converted
        user_def = combined_schema["$defs"]["User"]
        assert user_def["properties"]["profile"]["$ref"] == "#/$defs/Profile"

    def test_refs_in_composition_keywords_converted(self):
        """$refs inside oneOf/anyOf/allOf must be converted from OpenAPI to JSON Schema format."""
        route = HTTPRoute(
            path="/data",
            method="POST",
            operation_id="create_data",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "oneOf": [
                                    {"$ref": "#/components/schemas/TypeA"},
                                    {"$ref": "#/components/schemas/TypeB"},
                                ]
                            },
                            "alternate": {
                                "anyOf": [
                                    {"$ref": "#/components/schemas/TypeC"},
                                    {"$ref": "#/components/schemas/TypeD"},
                                ]
                            },
                            "combined": {
                                "allOf": [
                                    {"$ref": "#/components/schemas/BaseType"},
                                    {"properties": {"extra": {"type": "string"}}},
                                ]
                            },
                        },
                    }
                },
            ),
            request_schemas={
                "TypeA": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/components/schemas/Nested"}},
                },
                "TypeB": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
                "TypeC": {"type": "string"},
                "TypeD": {"type": "number"},
                "BaseType": {
                    "type": "object",
                    "properties": {"base": {"type": "string"}},
                },
                "Nested": {"type": "string"},
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # oneOf refs should be converted
        oneof_refs = combined_schema["properties"]["data"]["oneOf"]
        assert oneof_refs[0]["$ref"] == "#/$defs/TypeA"
        assert oneof_refs[1]["$ref"] == "#/$defs/TypeB"

        # anyOf refs should be converted
        anyof_refs = combined_schema["properties"]["alternate"]["anyOf"]
        assert anyof_refs[0]["$ref"] == "#/$defs/TypeC"
        assert anyof_refs[1]["$ref"] == "#/$defs/TypeD"

        # allOf refs should be converted
        allof_refs = combined_schema["properties"]["combined"]["allOf"]
        assert allof_refs[0]["$ref"] == "#/$defs/BaseType"

        # Transitive refs should be converted
        type_a_def = combined_schema["$defs"]["TypeA"]
        assert type_a_def["properties"]["nested"]["$ref"] == "#/$defs/Nested"

    def test_deeply_nested_transitive_refs_preserved(self):
        """Deeply nested transitive refs (A→B→C→D→E) must all be preserved."""
        route = HTTPRoute(
            path="/deep",
            method="POST",
            operation_id="create_deep",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/Level1"}
                },
            ),
            request_schemas={
                "Level1": {
                    "type": "object",
                    "properties": {"level2": {"$ref": "#/components/schemas/Level2"}},
                },
                "Level2": {
                    "type": "object",
                    "properties": {"level3": {"$ref": "#/components/schemas/Level3"}},
                },
                "Level3": {
                    "type": "object",
                    "properties": {"level4": {"$ref": "#/components/schemas/Level4"}},
                },
                "Level4": {
                    "type": "object",
                    "properties": {"level5": {"$ref": "#/components/schemas/Level5"}},
                },
                "Level5": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
                "UnusedSchema": {"type": "number"},
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # All levels should be preserved
        assert "Level1" in combined_schema["$defs"]
        assert "Level2" in combined_schema["$defs"]
        assert "Level3" in combined_schema["$defs"]
        assert "Level4" in combined_schema["$defs"]
        assert "Level5" in combined_schema["$defs"]

        # Unused should be removed (pruning is allowed for unused schemas)
        assert "UnusedSchema" not in combined_schema["$defs"]

        # All refs should be converted
        assert (
            combined_schema["$defs"]["Level1"]["properties"]["level2"]["$ref"]
            == "#/$defs/Level2"
        )
        assert (
            combined_schema["$defs"]["Level2"]["properties"]["level3"]["$ref"]
            == "#/$defs/Level3"
        )
        assert (
            combined_schema["$defs"]["Level3"]["properties"]["level4"]["$ref"]
            == "#/$defs/Level4"
        )
        assert (
            combined_schema["$defs"]["Level4"]["properties"]["level5"]["$ref"]
            == "#/$defs/Level5"
        )

    def test_circular_references_handled(self):
        """Circular references (A→B→A) must be handled without infinite loops."""
        route = HTTPRoute(
            path="/circular",
            method="POST",
            operation_id="circular_test",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/Node"}
                },
            ),
            request_schemas={
                "Node": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # Node should be preserved
        assert "Node" in combined_schema["$defs"]

        # Self-reference should be converted
        node_def = combined_schema["$defs"]["Node"]
        assert node_def["properties"]["children"]["items"]["$ref"] == "#/$defs/Node"

    def test_multiple_reference_paths_to_same_schema(self):
        """Multiple paths to the same schema (diamond pattern) must preserve the schema."""
        route = HTTPRoute(
            path="/diamond",
            method="POST",
            operation_id="diamond_test",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            "left": {"$ref": "#/components/schemas/Left"},
                            "right": {"$ref": "#/components/schemas/Right"},
                        },
                    }
                },
            ),
            request_schemas={
                "Left": {
                    "type": "object",
                    "properties": {"shared": {"$ref": "#/components/schemas/Shared"}},
                },
                "Right": {
                    "type": "object",
                    "properties": {"shared": {"$ref": "#/components/schemas/Shared"}},
                },
                "Shared": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # All schemas should be preserved
        assert "Left" in combined_schema["$defs"]
        assert "Right" in combined_schema["$defs"]
        assert "Shared" in combined_schema["$defs"]

        # All refs should be converted
        assert combined_schema["properties"]["left"]["$ref"] == "#/$defs/Left"
        assert combined_schema["properties"]["right"]["$ref"] == "#/$defs/Right"
        assert (
            combined_schema["$defs"]["Left"]["properties"]["shared"]["$ref"]
            == "#/$defs/Shared"
        )
        assert (
            combined_schema["$defs"]["Right"]["properties"]["shared"]["$ref"]
            == "#/$defs/Shared"
        )

    def test_refs_in_nested_content_schemas(self):
        """$refs in nested content schemas (the original bug location) must be converted."""
        route = HTTPRoute(
            path="/content",
            method="POST",
            operation_id="content_test",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {"$ref": "#/components/schemas/Content"}
                },
            ),
            request_schemas={
                "Content": {
                    "type": "object",
                    "properties": {
                        "media": {
                            "type": "object",
                            "properties": {
                                "application/json": {
                                    "$ref": "#/components/schemas/JsonContent"
                                }
                            },
                        }
                    },
                },
                "JsonContent": {
                    "type": "object",
                    "properties": {"data": {"type": "string"}},
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # Both schemas should be preserved
        assert "Content" in combined_schema["$defs"]
        assert "JsonContent" in combined_schema["$defs"]

        # Nested ref should be converted
        content_def = combined_schema["$defs"]["Content"]
        nested_ref = content_def["properties"]["media"]["properties"][
            "application/json"
        ]
        assert nested_ref["$ref"] == "#/$defs/JsonContent"

    def test_unnecessary_defs_preserved_when_referenced(self):
        """Even seemingly unnecessary $defs must be preserved if they're referenced."""
        route = HTTPRoute(
            path="/test",
            method="POST",
            operation_id="test_unnecessary",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    "application/json": {
                        "type": "object",
                        "properties": {
                            # Reference to a simple type schema
                            "simple": {"$ref": "#/components/schemas/SimpleString"},
                            # Reference to an empty object schema
                            "empty": {"$ref": "#/components/schemas/EmptyObject"},
                        },
                    }
                },
            ),
            request_schemas={
                "SimpleString": {"type": "string"},
                "EmptyObject": {"type": "object"},
                "UnreferencedSchema": {"type": "number"},
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # Referenced schemas should be preserved even if simple
        assert "SimpleString" in combined_schema["$defs"]
        assert "EmptyObject" in combined_schema["$defs"]

        # Unreferenced should be removed
        assert "UnreferencedSchema" not in combined_schema["$defs"]

        # Refs should be converted
        assert combined_schema["properties"]["simple"]["$ref"] == "#/$defs/SimpleString"
        assert combined_schema["properties"]["empty"]["$ref"] == "#/$defs/EmptyObject"

    def test_ref_only_request_body_handled(self):
        """Request bodies that are just a $ref (not an object with properties) must work."""
        route = HTTPRoute(
            path="/direct-ref",
            method="POST",
            operation_id="direct_ref_test",
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    # Direct $ref, not wrapped in an object
                    "application/json": {"$ref": "#/components/schemas/DirectBody"}
                },
            ),
            request_schemas={
                "DirectBody": {
                    "type": "object",
                    "properties": {
                        "field1": {"type": "string"},
                        "nested": {"$ref": "#/components/schemas/NestedBody"},
                    },
                },
                "NestedBody": {
                    "type": "object",
                    "properties": {"field2": {"type": "number"}},
                },
            },
        )

        combined_schema, _ = _combine_schemas_and_map_params(route)

        # Should handle the direct ref properly
        assert "body" in combined_schema["properties"]
        assert combined_schema["properties"]["body"]["$ref"] == "#/$defs/DirectBody"

        # Both schemas should be preserved
        assert "DirectBody" in combined_schema["$defs"]
        assert "NestedBody" in combined_schema["$defs"]

        # Nested ref should be converted
        assert (
            combined_schema["$defs"]["DirectBody"]["properties"]["nested"]["$ref"]
            == "#/$defs/NestedBody"
        )

    def test_separate_input_output_schemas(self):
        """Test that input and output schemas contain different schema
        definitions and don't overlap in the ultimate schema definitions."""
        # OpenAPI spec with transitive dependencies to force schema inclusion
        openapi_spec = {
            "openapi": "3.0.1",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "post": {
                        "summary": "Test endpoint",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/InputContainer"
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Success",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/OutputContainer"
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "InputContainer": {
                        "type": "object",
                        "properties": {
                            "data": {"$ref": "#/components/schemas/InputData"}
                        },
                    },
                    "InputData": {
                        "type": "object",
                        "properties": {"input_field": {"type": "string"}},
                    },
                    "OutputContainer": {
                        "type": "object",
                        "properties": {
                            "result": {"$ref": "#/components/schemas/OutputData"}
                        },
                    },
                    "OutputData": {
                        "type": "object",
                        "properties": {"output_field": {"type": "string"}},
                    },
                    "UnusedSchema": {
                        "type": "object",
                        "properties": {"unused_field": {"type": "string"}},
                    },
                }
            },
        }

        routes = parse_openapi_to_http_routes(openapi_spec)
        assert len(routes) == 1

        route = routes[0]

        # Check that schemas are properly separated
        input_schema_names = set(route.request_schemas.keys())
        output_schema_names = set(route.response_schemas.keys())

        # Input should contain transitive dependencies from InputContainer
        assert "InputData" in input_schema_names, (
            f"Expected InputData in request schemas: {input_schema_names}"
        )
        assert "OutputContainer" not in input_schema_names, (
            "OutputContainer should not be in request schemas"
        )
        assert "OutputData" not in input_schema_names, (
            "OutputData should not be in request schemas"
        )

        # Output should contain transitive dependencies from OutputContainer
        assert "OutputData" in output_schema_names, (
            f"Expected OutputData in response schemas: {output_schema_names}"
        )
        assert "InputContainer" not in output_schema_names, (
            "InputContainer should not be in response schemas"
        )
        assert "InputData" not in output_schema_names, (
            "InputData should not be in response schemas"
        )

        # Neither should contain unused schema
        assert "UnusedSchema" not in input_schema_names, (
            "UnusedSchema should not be in request schemas"
        )
        assert "UnusedSchema" not in output_schema_names, (
            "UnusedSchema should not be in response schemas"
        )

        # Verify no overlap
        overlap = input_schema_names & output_schema_names
        assert len(overlap) == 0, (
            f"Found overlapping schemas between input and output: {overlap}"
        )

    def test_issue_2087_top_level_response_ref_includes_all_nested_schemas(self):
        """Issue #2087: Top-level response $ref must include itself in response_schemas."""
        spec = {
            "openapi": "3.0.1",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/persons": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/PersonList"
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "PersonList": {
                        "properties": {
                            "Items": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Person"},
                            }
                        }
                    },
                    "Person": {
                        "properties": {
                            "Name": {"$ref": "#/components/schemas/Name"},
                            "Job": {"$ref": "#/components/schemas/Job"},
                        }
                    },
                    "Name": {"properties": {"First": {"type": "string"}}},
                    "Job": {"properties": {"Company": {"type": "string"}}},
                }
            },
        }

        routes = parse_openapi_to_http_routes(spec)
        route = routes[0]

        # Bug was: PersonList missing from response_schemas despite being top-level ref
        assert "PersonList" in route.response_schemas
        assert "Person" in route.response_schemas
        assert "Name" in route.response_schemas
        assert "Job" in route.response_schemas
