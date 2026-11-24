"""Regression coverage for descriptions in secondary parameter sections."""

from typing import Annotated

import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.utilities.docstring_parsing import parse_docstring


@pytest.fixture(params=["google", "numpy"])
def parameter_docstring(request: pytest.FixtureRequest) -> str:
    if request.param == "google":
        return """Search the catalog.

        Args:
            query: Text to search for.

        Keyword Args:
            limit: Maximum number of results.
            ghost: A documented name outside the signature.
        """
    return """Search the catalog.

    Parameters
    ----------
    query : str
        Text to search for.

    Other Parameters
    ----------------
    limit : int
        Maximum number of results.
    ghost : str
        A documented name outside the signature.
    """


def test_secondary_section_descriptions_are_collected(parameter_docstring: str) -> None:
    def search(query: str, *, limit: int = 10) -> list[str]:
        return [query] * limit

    search.__doc__ = parameter_docstring
    parsed = parse_docstring(search)
    assert parsed.description == "Search the catalog."
    assert parsed.parameters == {
        "query": "Text to search for.",
        "limit": "Maximum number of results.",
        "ghost": "A documented name outside the signature.",
    }


async def test_client_tool_schema_and_calls(parameter_docstring: str) -> None:
    def search(query: str, *, limit: int = 10) -> list[str]:
        return [query] * limit

    search.__doc__ = parameter_docstring
    server = FastMCP("secondary-section-tools")
    server.tool(search)
    async with Client(server) as client:
        tool = (await client.list_tools())[0]
        properties = tool.input_schema["properties"]
        assert properties["query"]["description"] == "Text to search for."
        assert properties["limit"]["description"] == "Maximum number of results."
        assert properties["limit"]["default"] == 10
        assert properties["limit"]["type"] == "integer"
        assert "ghost" not in properties
        assert (await client.call_tool("search", {"query": "x"})).data == ["x"] * 10
        assert (await client.call_tool("search", {"query": "x", "limit": 2})).data == [
            "x"
        ] * 2


def test_explicit_description_precedes_secondary_section(
    parameter_docstring: str,
) -> None:
    def search(
        query: str,
        *,
        limit: Annotated[int, Field(description="Explicit limit description")] = 10,
    ) -> list[str]:
        return [query] * limit

    search.__doc__ = parameter_docstring
    parsed = ParsedFunction.from_function(search)
    assert (
        parsed.input_schema["properties"]["limit"]["description"]
        == "Explicit limit description"
    )
    assert "ghost" not in parsed.input_schema["properties"]


async def test_client_prompt_arguments(parameter_docstring: str) -> None:
    def summarize(query: str, *, limit: int = 10) -> str:
        return f"Summarize {query} in {limit} items."

    summarize.__doc__ = parameter_docstring
    server = FastMCP("secondary-section-prompts")
    server.prompt(summarize)
    async with Client(server) as client:
        prompt = (await client.list_prompts())[0]
        arguments = {argument.name: argument for argument in prompt.arguments or []}
        description = arguments["limit"].description
        assert description is not None
        assert description.startswith("Maximum number of results.")
        assert "Encode non-string values as JSON." in description
        assert "ghost" not in arguments


@pytest.mark.parametrize(
    "docstring",
    [
        pytest.param(
            "Search.\n\nKeyword Args:\n    limit: Maximum number of results.",
            id="google-only",
        ),
        pytest.param(
            "Search.\n\nOther Parameters\n----------------\nlimit : int\n    Maximum number of results.",
            id="numpy-only",
        ),
    ],
)
def test_secondary_section_without_primary_section(docstring: str) -> None:
    def search(*, limit: int = 10) -> int:
        return limit

    search.__doc__ = docstring
    parsed = parse_docstring(search)
    assert parsed.description == "Search."
    assert parsed.parameters == {"limit": "Maximum number of results."}
