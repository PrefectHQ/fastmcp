import pytest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import MCPError
from fastmcp.resources.template import (
    expand_uri_template,
    forward_uri,
    match_uri_template,
)


def test_match_literal_query_with_continuation_params():
    template = "search://items?fixed=1{&limit,offset}"
    assert match_uri_template("search://items?fixed=1", template) == {}
    assert match_uri_template("search://items?fixed=1&limit=10", template) == {
        "limit": "10"
    }
    assert match_uri_template(
        "search://items?fixed=1&limit=10&offset=20", template
    ) == {"limit": "10", "offset": "20"}
    assert match_uri_template("search://items?fixed=10", template) is None
    assert match_uri_template("search://items?fixed=2&limit=10", template) is None


def test_match_literal_query_placeholder_with_continuation_and_round_trip():
    template = "search://items?q={q}{&limit,offset}"
    expanded = expand_uri_template(template, {"q": "hello/world", "limit": "10"})
    assert expanded == "search://items?q=hello%2Fworld&limit=10"
    assert match_uri_template(expanded, template) == {
        "q": "hello/world",
        "limit": "10",
    }
    assert match_uri_template("search://items?q=hello", template) == {"q": "hello"}


def test_match_literal_query_with_exploded_continuation():
    template = "search://items?fixed=1{&tags*}"
    assert match_uri_template("search://items?fixed=1&tags=a&tags=b", template) == {
        "tags": ["a", "b"]
    }


def test_literal_query_without_continuation_still_rejects_extra_params():
    template = "search://items?q={q}"
    assert match_uri_template("search://items?q=hello", template) == {"q": "hello"}
    assert match_uri_template("search://items?q=hello&extra=1", template) is None


def test_forward_uri_preserves_continuation_tail_for_literal_query():
    template = "search://items?q={q}{&limit}"
    forwarded = forward_uri(
        template,
        {"q": "hello"},
        "search://sub/items?q=hello&limit=10",
    )
    assert forwarded == "search://items?q=hello&limit=10"
    assert (
        forward_uri(template, {"q": "hello"}, "search://sub/items?q=hello")
        == "search://items?q=hello"
    )


async def test_client_reads_literal_query_continuation_templates_and_mounts():
    sub = FastMCP("sub")

    @sub.resource("search://items?fixed=1{&limit}")
    def search_fixed(limit: int = 5) -> str:
        return f"limit={limit}"

    @sub.resource("search://items?q={q}{&limit}")
    def search_q(q: str, limit: int = 5) -> str:
        return f"q={q} limit={limit}"

    parent = FastMCP("parent")
    parent.mount(sub, "sub")

    async with Client(sub) as client:
        res1 = await client.read_resource("search://items?fixed=1&limit=10")
        assert res1[0].text == "limit=10"
        res2 = await client.read_resource("search://items?q=hello&limit=10")
        assert res2[0].text == "q=hello limit=10"
        res3 = await client.read_resource("search://items?q=hello")
        assert res3[0].text == "q=hello limit=5"
        with pytest.raises(MCPError):
            await client.read_resource("search://items?fixed=2&limit=10")

    async with Client(parent) as client:
        res_mounted = await client.read_resource("search://sub/items?q=hello&limit=10")
        assert res_mounted[0].text == "q=hello limit=10"
