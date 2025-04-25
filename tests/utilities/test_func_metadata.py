from typing import Annotated

import annotated_types
import pytest
from pydantic import BaseModel, Field

from fastmcp.utilities.func_metadata import func_metadata


class SomeInputModelA(BaseModel):
    pass


class SomeInputModelB(BaseModel):
    class InnerModel(BaseModel):
        x: int

    how_many_shrimp: Annotated[int, Field(description="How many shrimp in the tank???")]
    ok: InnerModel
    y: None


def complex_arguments_fn(
    an_int: int,
    must_be_none: None,
    must_be_none_dumb_annotation: Annotated[None, "blah"],
    list_of_ints: list[int],
    # list[str] | str is an interesting case because if it comes in as JSON like
    # "[\"a\", \"b\"]" then it will be naively parsed as a string.
    list_str_or_str: list[str] | str,
    an_int_annotated_with_field: Annotated[
        int, Field(description="An int with a field")
    ],
    an_int_annotated_with_field_and_others: Annotated[
        int,
        str,  # Should be ignored, really
        Field(description="An int with a field"),
        annotated_types.Gt(1),
    ],
    an_int_annotated_with_junk: Annotated[
        int,
        "123",
        456,
    ],
    field_with_default_via_field_annotation_before_nondefault_arg: Annotated[
        int, Field(1)
    ],
    unannotated,
    my_model_a: SomeInputModelA,
    my_model_a_forward_ref: "SomeInputModelA",
    my_model_b: SomeInputModelB,
    an_int_annotated_with_field_default: Annotated[
        int,
        Field(1, description="An int with a field"),
    ],
    unannotated_with_default=5,
    my_model_a_with_default: SomeInputModelA = SomeInputModelA(),  # noqa: B008
    an_int_with_default: int = 1,
    must_be_none_with_default: None = None,
    an_int_with_equals_field: int = Field(1, ge=0),
    int_annotated_with_default: Annotated[int, Field(description="hey")] = 5,
) -> str:
    _ = (
        an_int,
        must_be_none,
        must_be_none_dumb_annotation,
        list_of_ints,
        list_str_or_str,
        an_int_annotated_with_field,
        an_int_annotated_with_field_and_others,
        an_int_annotated_with_junk,
        field_with_default_via_field_annotation_before_nondefault_arg,
        unannotated,
        an_int_annotated_with_field_default,
        unannotated_with_default,
        my_model_a,
        my_model_a_forward_ref,
        my_model_b,
        my_model_a_with_default,
        an_int_with_default,
        must_be_none_with_default,
        an_int_with_equals_field,
        int_annotated_with_default,
    )
    return "ok!"


async def test_complex_function_runtime_arg_validation_non_json():
    """Test that basic non-JSON arguments are validated correctly"""
    meta = func_metadata(complex_arguments_fn)

    # Test with minimum required arguments
    result = await meta.call_fn_with_arg_validation(
        complex_arguments_fn,
        fn_is_async=False,
        arguments_to_validate={
            "an_int": 1,
            "must_be_none": None,
            "must_be_none_dumb_annotation": None,
            "list_of_ints": [1, 2, 3],
            "list_str_or_str": "hello",
            "an_int_annotated_with_field": 42,
            "an_int_annotated_with_field_and_others": 5,
            "an_int_annotated_with_junk": 100,
            "unannotated": "test",
            "my_model_a": {},
            "my_model_a_forward_ref": {},
            "my_model_b": {"how_many_shrimp": 5, "ok": {"x": 1}, "y": None},
        },
        arguments_to_pass_directly=None,
    )
    assert result == "ok!"

    # Test with invalid types
    with pytest.raises(ValueError):
        await meta.call_fn_with_arg_validation(
            complex_arguments_fn,
            fn_is_async=False,
            arguments_to_validate={"an_int": "not an int"},
            arguments_to_pass_directly=None,
        )


async def test_complex_function_runtime_arg_validation_with_json():
    """Test that JSON string arguments are parsed and validated correctly"""
    meta = func_metadata(complex_arguments_fn)

    result = await meta.call_fn_with_arg_validation(
        complex_arguments_fn,
        fn_is_async=False,
        arguments_to_validate={
            "an_int": 1,
            "must_be_none": None,
            "must_be_none_dumb_annotation": None,
            "list_of_ints": "[1, 2, 3]",  # JSON string
            "list_str_or_str": '["a", "b", "c"]',  # JSON string
            "an_int_annotated_with_field": 42,
            "an_int_annotated_with_field_and_others": "5",  # JSON string
            "an_int_annotated_with_junk": 100,
            "unannotated": "test",
            "my_model_a": "{}",  # JSON string
            "my_model_a_forward_ref": "{}",  # JSON string
            "my_model_b": '{"how_many_shrimp": 5, "ok": {"x": 1}, "y": null}',
        },
        arguments_to_pass_directly=None,
    )
    assert result == "ok!"


def test_str_vs_list_str():
    """Test handling of string vs list[str] type annotations.

    This is tricky as '"hello"' can be parsed as a JSON string or a Python string.
    We want to make sure it's kept as a python string.
    """

    def func_with_str_types(str_or_list: str | list[str]):
        return str_or_list

    meta = func_metadata(func_with_str_types)

    # Test string input for union type
    result = meta.pre_parse_json({"str_or_list": "hello"})
    assert result["str_or_list"] == "hello"

    # Test string input that contains valid JSON for union type
    # We want to see here that the JSON-vali string is NOT parsed as JSON, but rather
    # kept as a raw string
    result = meta.pre_parse_json({"str_or_list": '"hello"'})
    assert result["str_or_list"] == '"hello"'

    # Test list input for union type
    result = meta.pre_parse_json({"str_or_list": '["hello", "world"]'})
    assert result["str_or_list"] == ["hello", "world"]


def test_keep_str_as_str():
    """Test that string arguments are kept as strings"""

    def func_with_str_types(string: str):
        return string

    meta = func_metadata(func_with_str_types)
    result = meta.pre_parse_json(
        {"string": "{'nice to meet you': 'hello', 'goodbye': 5}"}
    )
    assert result["string"] == "{'nice to meet you': 'hello', 'goodbye': 5}"


def test_missing_annotation():
    """Test that missing annotations don't cause errors"""

    def fn(x, y):
        return x + y

    meta = func_metadata(fn)
    result = meta.pre_parse_json({"x": "1", "y": "2"})
    assert result["x"] == "1"
    assert result["y"] == "2"


def test_keep_str_union_as_str():
    """Test that string arguments are kept as strings"""

    def func_with_str_types(string: str | dict[int, str] | None):
        return string

    meta = func_metadata(func_with_str_types)
    result = meta.pre_parse_json(
        {"string": "{'nice to meet you': 'hello', 'goodbye': 5}"}
    )
    assert result["string"] == "{'nice to meet you': 'hello', 'goodbye': 5}"


def test_keep_str_complex_type_as_str():
    """Test that string arguments are kept as strings because it's invalid for the field"""

    class SomeModel(BaseModel):
        x: int
        y: dict[int, str]

    def func_with_str_types(string: str | SomeModel | None):
        return string

    meta = func_metadata(func_with_str_types)
    result = meta.pre_parse_json({"string": '{"x": 1, "y": {"invalid": "hello"}}'})
    assert result["string"] == '{"x": 1, "y": {"invalid": "hello"}}'


def test_convert_str_to_complex_type():
    """Test that string arguments are converted to the complex type because it's valid for the field"""

    class SomeModel(BaseModel):
        x: int
        y: dict[int, str]

    def func_with_str_types(string: str | SomeModel | None):
        return string

    meta = func_metadata(func_with_str_types)
    result = meta.pre_parse_json({"string": '{"x": 1, "y": {"1": "hello"}}'})
    assert result["string"] == {"x": 1, "y": {"1": "hello"}}


def test_skip_names():
    """Test that skipped parameters are not included in the model"""

    def func_with_many_params(
        keep_this: int, skip_this: str, also_keep: float, also_skip: bool
    ):
        return keep_this, skip_this, also_keep, also_skip

    # Skip some parameters
    meta = func_metadata(func_with_many_params, skip_names=["skip_this", "also_skip"])

    # Check model fields
    assert "keep_this" in meta.arg_model.model_fields
    assert "also_keep" in meta.arg_model.model_fields
    assert "skip_this" not in meta.arg_model.model_fields
    assert "also_skip" not in meta.arg_model.model_fields

    # Validate that we can call with only non-skipped parameters
    model: BaseModel = meta.arg_model.model_validate({"keep_this": 1, "also_keep": 2.5})  # type: ignore
    assert model.keep_this == 1  # type: ignore
    assert model.also_keep == 2.5  # type: ignore


async def test_lambda_function():
    """Test lambda function schema and validation"""
    fn = lambda x, y=5: x  # noqa: E731
    meta = func_metadata(lambda x, y=5: x)

    # Test schema
    assert meta.arg_model.model_json_schema() == {
        "properties": {
            "x": {"title": "x", "type": "string"},
            "y": {"default": 5, "title": "y", "type": "string"},
        },
        "required": ["x"],
        "title": "<lambda>Arguments",
        "type": "object",
    }

    async def check_call(args):
        return await meta.call_fn_with_arg_validation(
            fn,
            fn_is_async=False,
            arguments_to_validate=args,
            arguments_to_pass_directly=None,
        )

    # Basic calls
    assert await check_call({"x": "hello"}) == "hello"
    assert await check_call({"x": "hello", "y": "world"}) == "hello"
    assert await check_call({"x": '"hello"'}) == '"hello"'

    # Missing required arg
    with pytest.raises(ValueError):
        await check_call({"y": "world"})


def test_complex_function_json_schema():
    """Test JSON schema generation for complex function arguments.

    Note: Different versions of pydantic output slightly different
    JSON Schema formats for model fields with defaults. The format changed in 2.9.0:

    1. Before 2.9.0:
       {
         "allOf": [{"$ref": "#/$defs/Model"}],
         "default": {}
       }

    2. Since 2.9.0:
       {
         "$ref": "#/$defs/Model",
         "default": {}
       }

    Both formats are valid and functionally equivalent. This test accepts either format
    to ensure compatibility across our supported pydantic versions.

    This change in format does not affect runtime behavior since:
    1. Both schemas validate the same way
    2. The actual model classes and validation logic are unchanged
    3. func_metadata uses model_validate/model_dump, not the schema directly
    """
    meta = func_metadata(complex_arguments_fn)
    actual_schema = meta.arg_model.model_json_schema()

    # Create a copy of the actual schema to normalize
    normalized_schema = actual_schema.copy()

    # Normalize the my_model_a_with_default field to handle both pydantic formats
    if "allOf" in actual_schema["properties"]["my_model_a_with_default"]:
        normalized_schema["properties"]["my_model_a_with_default"] = {
            "$ref": "#/$defs/SomeInputModelA",
            "default": {},
        }

    assert normalized_schema == {
        "$defs": {
            "InnerModel": {
                "properties": {"x": {"title": "X", "type": "integer"}},
                "required": ["x"],
                "title": "InnerModel",
                "type": "object",
            },
            "SomeInputModelA": {
                "properties": {},
                "title": "SomeInputModelA",
                "type": "object",
            },
            "SomeInputModelB": {
                "properties": {
                    "how_many_shrimp": {
                        "description": "How many shrimp in the tank???",
                        "title": "How Many Shrimp",
                        "type": "integer",
                    },
                    "ok": {"$ref": "#/$defs/InnerModel"},
                    "y": {"title": "Y", "type": "null"},
                },
                "required": ["how_many_shrimp", "ok", "y"],
                "title": "SomeInputModelB",
                "type": "object",
            },
        },
        "properties": {
            "an_int": {"title": "An Int", "type": "integer"},
            "must_be_none": {"title": "Must Be None", "type": "null"},
            "must_be_none_dumb_annotation": {
                "title": "Must Be None Dumb Annotation",
                "type": "null",
            },
            "list_of_ints": {
                "items": {"type": "integer"},
                "title": "List Of Ints",
                "type": "array",
            },
            "list_str_or_str": {
                "anyOf": [
                    {"items": {"type": "string"}, "type": "array"},
                    {"type": "string"},
                ],
                "title": "List Str Or Str",
            },
            "an_int_annotated_with_field": {
                "description": "An int with a field",
                "title": "An Int Annotated With Field",
                "type": "integer",
            },
            "an_int_annotated_with_field_and_others": {
                "description": "An int with a field",
                "exclusiveMinimum": 1,
                "title": "An Int Annotated With Field And Others",
                "type": "integer",
            },
            "an_int_annotated_with_junk": {
                "title": "An Int Annotated With Junk",
                "type": "integer",
            },
            "field_with_default_via_field_annotation_before_nondefault_arg": {
                "default": 1,
                "title": "Field With Default Via Field Annotation Before Nondefault Arg",
                "type": "integer",
            },
            "unannotated": {"title": "unannotated", "type": "string"},
            "my_model_a": {"$ref": "#/$defs/SomeInputModelA"},
            "my_model_a_forward_ref": {"$ref": "#/$defs/SomeInputModelA"},
            "my_model_b": {"$ref": "#/$defs/SomeInputModelB"},
            "an_int_annotated_with_field_default": {
                "default": 1,
                "description": "An int with a field",
                "title": "An Int Annotated With Field Default",
                "type": "integer",
            },
            "unannotated_with_default": {
                "default": 5,
                "title": "unannotated_with_default",
                "type": "string",
            },
            "my_model_a_with_default": {
                "$ref": "#/$defs/SomeInputModelA",
                "default": {},
            },
            "an_int_with_default": {
                "default": 1,
                "title": "An Int With Default",
                "type": "integer",
            },
            "must_be_none_with_default": {
                "default": None,
                "title": "Must Be None With Default",
                "type": "null",
            },
            "an_int_with_equals_field": {
                "default": 1,
                "minimum": 0,
                "title": "An Int With Equals Field",
                "type": "integer",
            },
            "int_annotated_with_default": {
                "default": 5,
                "description": "hey",
                "title": "Int Annotated With Default",
                "type": "integer",
            },
        },
        "required": [
            "an_int",
            "must_be_none",
            "must_be_none_dumb_annotation",
            "list_of_ints",
            "list_str_or_str",
            "an_int_annotated_with_field",
            "an_int_annotated_with_field_and_others",
            "an_int_annotated_with_junk",
            "unannotated",
            "my_model_a",
            "my_model_a_forward_ref",
            "my_model_b",
        ],
        "title": "complex_arguments_fnArguments",
        "type": "object",
    }


def test_str_vs_int():
    """
    Test that string values are kept as strings even when they contain numbers,
    while numbers are parsed correctly.
    """

    def func_with_str_and_int(a: str, b: int):
        return a

    meta = func_metadata(func_with_str_and_int)
    result = meta.pre_parse_json({"a": "123", "b": 123})
    assert result["a"] == "123"
    assert result["b"] == 123
