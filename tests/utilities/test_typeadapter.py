"""
This test file adapts tests from test_func_metadata.py which tested a custom implementation
that has been replaced by pydantic TypeAdapters.

The tests ensure our TypeAdapter-based approach covers all the edge cases the old custom
implementation handled. Since we're now using standard pydantic functionality, these tests
may be redundant with pydantic's own tests and could potentially be removed in the future.
"""

from typing import Annotated

import annotated_types
import pytest
from pydantic import BaseModel, Field

from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.types import get_cached_typeadapter


# Models must be defined at the module level for forward references to work
class SomeInputModelA(BaseModel):
    pass


class SomeInputModelB(BaseModel):
    class InnerModel(BaseModel):
        x: int

    how_many_shrimp: Annotated[int, Field(description="How many shrimp in the tank???")]
    ok: InnerModel
    y: None


# Define additional models needed in tests
class SomeComplexModel(BaseModel):
    x: int
    y: dict[int, str]


class ClassWithMethods:
    def do_something(self, x: int) -> int:
        return x

    def do_something_annotated(
        self, x: Annotated[int, Field(description="A description")]
    ) -> int:
        return x

    def do_something_return_none(self) -> None:
        return None


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
        int, Field(default=1)
    ],
    unannotated,
    my_model_a: SomeInputModelA,
    my_model_a_forward_ref: "SomeInputModelA",
    my_model_b: SomeInputModelB,
    an_int_annotated_with_field_default: Annotated[
        int,
        Field(default=1, description="An int with a field"),
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


def get_simple_func_adapter():
    """Get a TypeAdapter for a simple function to avoid forward reference issues"""

    def simple_func(x: int, y: str = "default") -> str:
        return f"{x}-{y}"

    return get_cached_typeadapter(simple_func)


async def test_complex_function_runtime_arg_validation_non_json():
    """Test that basic non-JSON arguments are validated correctly using a simpler function"""
    type_adapter = get_simple_func_adapter()

    # Test with minimum required arguments
    args = {"x": 1}
    result = type_adapter.validate_python(args)
    assert (
        result == "1-default"
    )  # Don't call result() as TypeAdapter returns the value directly

    # Test with all arguments
    args = {"x": 1, "y": "hello"}
    result = type_adapter.validate_python(args)
    assert result == "1-hello"

    # Test with invalid types
    with pytest.raises(Exception):
        type_adapter.validate_python({"x": "not an int"})


def test_missing_annotation():
    """Test that missing annotations don't cause errors"""

    def func_no_annotations(x, y):
        return x + y

    type_adapter = get_cached_typeadapter(func_no_annotations)
    result = type_adapter.validate_python({"x": "1", "y": "2"})
    assert result == "12"  # String concatenation since no type info


def test_convert_str_to_complex_type():
    """Test that string arguments are converted to the complex type when valid"""

    def func_with_str_types(string: SomeComplexModel):
        return string

    # Create a valid model instance
    input_data = {"x": 1, "y": {1: "hello"}}

    # Validate with model directly
    SomeComplexModel.model_validate(input_data)

    # Now check if type adapter validates correctly
    type_adapter = get_cached_typeadapter(func_with_str_types)
    result = type_adapter.validate_python({"string": input_data})

    assert isinstance(result, SomeComplexModel)
    assert result.x == 1
    assert result.y == {1: "hello"}


def test_skip_names():
    """Test that skipped parameters are not included in the schema"""

    def func_with_many_params(
        keep_this: int, skip_this: str, also_keep: float, also_skip: bool
    ):
        return keep_this, skip_this, also_keep, also_skip

    # Get schema and prune parameters
    type_adapter = get_cached_typeadapter(func_with_many_params)
    schema = type_adapter.json_schema()
    pruned_schema = compress_schema(schema, prune_params=["skip_this", "also_skip"])

    # Check that only the desired parameters remain
    assert "keep_this" in pruned_schema["properties"]
    assert "also_keep" in pruned_schema["properties"]
    assert "skip_this" not in pruned_schema["properties"]
    assert "also_skip" not in pruned_schema["properties"]

    # The pruned parameters should also be removed from required
    if "required" in pruned_schema:
        assert "skip_this" not in pruned_schema["required"]
        assert "also_skip" not in pruned_schema["required"]


async def test_lambda_function():
    """Test lambda function schema and validation"""
    fn = lambda x, y=5: str(x)  # noqa: E731
    type_adapter = get_cached_typeadapter(fn)

    # Basic calls - validate_python returns the result directly
    result = type_adapter.validate_python({"x": "hello"})
    assert result == "hello"

    result = type_adapter.validate_python({"x": "hello", "y": "world"})
    assert result == "hello"

    # Missing required arg
    with pytest.raises(Exception):
        type_adapter.validate_python({"y": "world"})


def test_basic_json_schema():
    """Test JSON schema generation for a simple function"""

    def simple_func(a: int, b: str = "default") -> str:
        return f"{a}-{b}"

    type_adapter = get_cached_typeadapter(simple_func)
    schema = type_adapter.json_schema()

    # Check basic properties
    assert "properties" in schema
    assert "a" in schema["properties"]
    assert "b" in schema["properties"]
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "string"
    assert "default" in schema["properties"]["b"]
    assert schema["properties"]["b"]["default"] == "default"

    # Check required
    assert "required" in schema
    assert "a" in schema["required"]
    assert "b" not in schema["required"]


def test_str_vs_int():
    """
    Test that string values are kept as strings even when they contain numbers,
    while numbers are parsed correctly.
    """

    def func_with_str_and_int(a: str, b: int):
        return a

    type_adapter = get_cached_typeadapter(func_with_str_and_int)
    result = type_adapter.validate_python({"a": "123", "b": 123})
    assert result == "123"


def test_class_with_methods():
    """Test that class methods are not included in the schema"""
    class_with_methods = ClassWithMethods()
    type_adapter = get_cached_typeadapter(class_with_methods.do_something)
    schema = type_adapter.json_schema()
    assert "self" not in schema["properties"]

    type_adapter = get_cached_typeadapter(class_with_methods.do_something_annotated)
    schema = type_adapter.json_schema()
    assert "self" not in schema["properties"]

    type_adapter = get_cached_typeadapter(class_with_methods.do_something_return_none)
    schema = type_adapter.json_schema()
    assert "self" not in schema["properties"]
