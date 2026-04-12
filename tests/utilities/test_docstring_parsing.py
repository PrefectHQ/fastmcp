"""Tests for docstring-to-schema parameter description extraction."""

from typing import Annotated

from pydantic import Field

from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.utilities.docstring_parsing import parse_docstring


class TestGoogleStyle:
    """Google-style docstrings (Args:/Arguments:)."""

    def test_basic(self):
        def fn(a: float, b: float) -> float:
            """Add two numbers.

            Args:
                a: The first number.
                b: The second number.
            """
            return a + b

        parsed = parse_docstring(fn)
        assert parsed.description == "Add two numbers."
        assert parsed.parameters == {
            "a": "The first number.",
            "b": "The second number.",
        }

    def test_with_inline_types(self):
        def fn(a: float, b: str) -> float:
            """Do something.

            Args:
                a (float): The number.
                b (str, optional): The string.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Do something."
        assert parsed.parameters == {"a": "The number.", "b": "The string."}

    def test_returns_section_excluded(self):
        def fn(a: float) -> float:
            """Summary.

            Args:
                a: The input.

            Returns:
                The output.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Summary."
        assert parsed.parameters == {"a": "The input."}

    def test_raises_section_excluded(self):
        def fn(a: float) -> float:
            """Summary.

            Args:
                a: The input.

            Raises:
                ValueError: If negative.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Summary."
        assert parsed.parameters == {"a": "The input."}

    def test_example_section_excluded(self):
        def fn(a: str) -> str:
            """Run some code.

            Example:
                >>> fn("hello")
                'hello'

            Args:
                a: The input.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Run some code."
        assert parsed.parameters == {"a": "The input."}

    def test_multiline_param_description(self):
        def fn(a: float) -> float:
            """Summary.

            Args:
                a: A description that
                    spans multiple lines.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Summary."
        assert "spans multiple lines" in parsed.parameters["a"]


class TestNumpyStyle:
    """NumPy-style docstrings (Parameters\\n----------)."""

    def test_basic(self):
        def fn(x: int, y: int) -> int:
            """Multiply.

            Parameters
            ----------
            x
                The first integer.
            y
                The second integer.
            """
            return x * y

        parsed = parse_docstring(fn)
        assert parsed.description == "Multiply."
        assert parsed.parameters == {
            "x": "The first integer.",
            "y": "The second integer.",
        }

    def test_with_types(self):
        def fn(a: float, b: str) -> float:
            """Do something.

            Parameters
            ----------
            a : float
                The number.
            b : str
                The string.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Do something."
        assert parsed.parameters == {"a": "The number.", "b": "The string."}


class TestSphinxStyle:
    """Sphinx-style docstrings (:param name:)."""

    def test_basic(self):
        def fn(name: str, age: int) -> str:
            """Format a greeting.

            :param name: The person's name.
            :param age: The person's age.
            """
            return f"{name} is {age}"

        parsed = parse_docstring(fn)
        assert parsed.description == "Format a greeting."
        assert parsed.parameters == {
            "name": "The person's name.",
            "age": "The person's age.",
        }

    def test_with_type_directive(self):
        def fn(a: float, b: str) -> float:
            """Summary.

            :param a: The number.
            :type a: float
            :param b: The string.
            :type b: str
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Summary."
        assert parsed.parameters == {"a": "The number.", "b": "The string."}


class TestEdgeCases:
    """Unusual, malformed, or partially-correct docstrings."""

    def test_no_docstring(self):
        def fn(a: int) -> int:
            return a

        parsed = parse_docstring(fn)
        assert parsed.description is None
        assert parsed.parameters == {}

    def test_summary_only(self):
        def fn(a: int) -> int:
            """Just a summary."""
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Just a summary."
        assert parsed.parameters == {}

    def test_multi_paragraph_description(self):
        def fn(a: float) -> float:
            """Summary line.

            More detailed explanation here
            spanning multiple lines.

            Another paragraph.

            Args:
                a: The number.
            """
            return a

        parsed = parse_docstring(fn)
        # Full description (summary + body) should be preserved
        assert parsed.description is not None
        assert "Summary line." in parsed.description
        assert "More detailed explanation" in parsed.description
        assert "Another paragraph." in parsed.description
        # Args section should not bleed into description
        assert "The number" not in parsed.description
        assert parsed.parameters == {"a": "The number."}

    def test_multiline_summary(self):
        def fn(a: float) -> float:
            """Multi-line summary
            continues on next line.

            Args:
                a: The number.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description is not None
        assert "Multi-line summary" in parsed.description
        assert "continues on next line" in parsed.description
        assert parsed.parameters == {"a": "The number."}

    def test_missing_colon_after_args_keyword(self):
        """Malformed: 'Args' without colon is not a valid section."""

        def fn(a: float) -> float:
            """Summary.

            Args
                a: Maybe the number?
            """
            return a

        parsed = parse_docstring(fn)
        # Parser shouldn't pick this up as an Args section
        assert parsed.parameters == {}

    def test_empty_args_section(self):
        def fn(a: float) -> float:
            """Summary.

            Args:
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.parameters == {}

    def test_param_name_not_in_function_signature(self):
        """Docstring documents a param that doesn't exist on the function."""

        def fn(a: float) -> float:
            """Summary.

            Args:
                nonexistent: Wrong param name.
            """
            return a

        parsed = parse_docstring(fn)
        # parse_docstring returns whatever the docstring says —
        # filtering happens at the schema injection level
        assert parsed.parameters == {"nonexistent": "Wrong param name."}

    def test_async_function(self):
        async def fn(a: float) -> float:
            """Async summary.

            Args:
                a: The number.
            """
            return a

        parsed = parse_docstring(fn)
        assert parsed.description == "Async summary."
        assert parsed.parameters == {"a": "The number."}


class TestParsedFunctionIntegration:
    """Tests for docstring flowing through ParsedFunction.from_function."""

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

    def test_numpy_style_integration(self):
        def fn(a: float, b: str) -> str:
            """Summary.

            Parameters
            ----------
            a : float
                The number.
            b : str
                The string.
            """
            return str(a) + b

        p = ParsedFunction.from_function(fn)
        assert p.description == "Summary."
        assert p.input_schema["properties"]["a"]["description"] == "The number."
        assert p.input_schema["properties"]["b"]["description"] == "The string."

    def test_sphinx_style_integration(self):
        def fn(a: float, b: str) -> str:
            """Summary.

            :param a: The number.
            :param b: The string.
            """
            return str(a) + b

        p = ParsedFunction.from_function(fn)
        assert p.description == "Summary."
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
        """Only some params documented — others remain undescribed."""

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

    def test_nonexistent_param_in_docstring_ignored(self):
        """Docstring mentions a param that doesn't exist — silently skipped."""

        def fn(a: float) -> float:
            """Summary.

            Args:
                a: The real one.
                ghost: Doesn't exist.
            """
            return a

        p = ParsedFunction.from_function(fn)
        assert p.input_schema["properties"]["a"]["description"] == "The real one."
        # No crash, no ghost in properties
        assert "ghost" not in p.input_schema["properties"]

    def test_types_in_docstring_dont_override_schema_types(self):
        """A '(str)' in the docstring must not change the schema's type."""

        def fn(a: float) -> float:
            """Summary.

            Args:
                a (str): A description, but the type is wrong.
            """
            return a

        p = ParsedFunction.from_function(fn)
        # Schema type comes from the annotation, not the docstring
        assert p.input_schema["properties"]["a"]["type"] == "number"
        assert (
            p.input_schema["properties"]["a"]["description"]
            == "A description, but the type is wrong."
        )

    def test_multi_paragraph_description_preserved(self):
        def fn(a: float) -> float:
            """Short summary.

            A longer explanation that provides
            additional context.

            Args:
                a: The number.
            """
            return a

        p = ParsedFunction.from_function(fn)
        assert p.description is not None
        assert "Short summary" in p.description
        assert "longer explanation" in p.description
        assert "The number" not in p.description

    def test_async_function_integration(self):
        async def fn(a: float) -> float:
            """Async summary.

            Args:
                a: The number.
            """
            return a

        p = ParsedFunction.from_function(fn)
        assert p.description == "Async summary."
        assert p.input_schema["properties"]["a"]["description"] == "The number."

    def test_callable_class_sources_description_from_class(self):
        """Class docstring drives the tool description (it describes what the
        tool IS), while __call__'s Args section drives parameter descriptions
        (its params are what the schema actually exposes)."""

        class MyTool:
            """Class-level description."""

            def __call__(self, x: int) -> int:
                """Internal call doc.

                Args:
                    x: From call.
                """
                return x

        p = ParsedFunction.from_function(MyTool())
        # Class docstring wins for the description
        assert p.description == "Class-level description."
        # __call__'s Args wins for the parameter description
        assert p.input_schema["properties"]["x"]["description"] == "From call."

    def test_callable_class_falls_back_to_call_description(self):
        """If the class has no docstring, fall back to __call__'s description."""

        class MyTool:
            def __call__(self, x: int) -> int:
                """Call-level description.

                Args:
                    x: From call.
                """
                return x

        p = ParsedFunction.from_function(MyTool())
        assert p.description == "Call-level description."
        assert p.input_schema["properties"]["x"]["description"] == "From call."
