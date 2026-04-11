"""Tests for docstring-to-schema parameter description extraction."""

from typing import Annotated

from pydantic import Field

from fastmcp.tools.docstring_parsing import parse_docstring
from fastmcp.tools.function_parsing import ParsedFunction


class TestParseDocstring:
    """Tests for the parse_docstring function."""

    def test_google_style(self):
        def fn(a: float, b: float) -> float:
            """Add two numbers.

            Args:
                a: The first number.
                b: The second number.
            """
            return a + b

        desc, params = parse_docstring(fn)
        assert desc == "Add two numbers."
        assert params == {"a": "The first number.", "b": "The second number."}

    def test_numpy_style(self):
        def fn(x: int, y: int) -> int:
            """Multiply two integers.

            Parameters
            ----------
            x
                The first integer.
            y
                The second integer.
            """
            return x * y

        desc, params = parse_docstring(fn)
        assert desc == "Multiply two integers."
        assert params == {"x": "The first integer.", "y": "The second integer."}

    def test_sphinx_style(self):
        def fn(name: str, age: int) -> str:
            """Format a greeting.

            :param name: The person's name.
            :param age: The person's age.
            """
            return f"{name} is {age}"

        desc, params = parse_docstring(fn)
        assert desc == "Format a greeting."
        assert params == {"name": "The person's name.", "age": "The person's age."}

    def test_no_docstring(self):
        def fn(a: int) -> int:
            return a

        desc, params = parse_docstring(fn)
        assert desc is None
        assert params == {}

    def test_no_params_section(self):
        def fn(a: int) -> int:
            """Just a summary."""
            return a

        desc, params = parse_docstring(fn)
        assert desc == "Just a summary."
        assert params == {}

    def test_multiline_param_description(self):
        def fn(a: float) -> float:
            """Do something.

            Args:
                a: A long description that
                    spans multiple lines.
            """
            return a

        desc, params = parse_docstring(fn)
        assert desc == "Do something."
        assert "long description" in params["a"]
        assert "multiple lines" in params["a"]


class TestParsedFunctionDocstrings:
    """Tests for docstring integration in ParsedFunction.from_function."""

    def test_description_is_summary_only(self):
        def fn(a: float) -> float:
            """The summary line.

            Args:
                a: Some param.

            Returns:
                Something.
            """
            return a

        p = ParsedFunction.from_function(fn)
        assert p.description == "The summary line."

    def test_param_descriptions_in_schema(self):
        def fn(a: float, b: str) -> str:
            """Do something.

            Args:
                a: The number.
                b: The string.
            """
            return str(a) + b

        p = ParsedFunction.from_function(fn)
        assert p.input_schema["properties"]["a"]["description"] == "The number."
        assert p.input_schema["properties"]["b"]["description"] == "The string."

    def test_field_description_takes_precedence(self):
        def fn(
            a: Annotated[float, Field(description="From Field")],
            b: float,
        ) -> float:
            """Add.

            Args:
                a: From docstring.
                b: Also from docstring.
            """
            return a + b

        p = ParsedFunction.from_function(fn)
        assert p.input_schema["properties"]["a"]["description"] == "From Field"
        assert (
            p.input_schema["properties"]["b"]["description"] == "Also from docstring."
        )

    def test_annotated_string_takes_precedence(self):
        def fn(
            a: Annotated[float, "From annotation"],
            b: float,
        ) -> float:
            """Add.

            Args:
                a: From docstring.
                b: Also from docstring.
            """
            return a + b

        p = ParsedFunction.from_function(fn)
        assert p.input_schema["properties"]["a"]["description"] == "From annotation"
        assert (
            p.input_schema["properties"]["b"]["description"] == "Also from docstring."
        )

    def test_no_docstring_no_descriptions(self):
        def fn(a: float) -> float:
            return a

        p = ParsedFunction.from_function(fn)
        assert p.description is None
        assert "description" not in p.input_schema["properties"]["a"]

    def test_docstring_without_args_section(self):
        def fn(a: float) -> float:
            """Just a summary."""
            return a

        p = ParsedFunction.from_function(fn)
        assert p.description == "Just a summary."
        assert "description" not in p.input_schema["properties"]["a"]

    def test_partial_params_documented(self):
        def fn(a: float, b: float, c: float) -> float:
            """Add numbers.

            Args:
                a: Documented.
            """
            return a + b + c

        p = ParsedFunction.from_function(fn)
        assert p.input_schema["properties"]["a"]["description"] == "Documented."
        assert "description" not in p.input_schema["properties"]["b"]
        assert "description" not in p.input_schema["properties"]["c"]
