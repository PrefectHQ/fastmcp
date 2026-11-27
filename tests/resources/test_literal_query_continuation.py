import pytest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import MCPError
from fastmcp.resources.template import (
    expand_uri_template,
    forward_uri,
    match_uri_template,
)
from fastmcp.server import create_proxy


def test_match_literal_query_with_continuation_params():
    template = "search://items?fixed=1{&limit,offset}"
    assert match_uri_template("search://items?fixed=1", template) == {}
    assert match_uri_template("search://items?fixed=1&limit=10", template) == {
        "limit": "10"
    }
    assert match_uri_template(
        "search://items?fixed=1&limit=10&offset=20", template
    ) == {"limit": "10", "offset": "20"}
    assert match_uri_template(
        "search://items?fixed=1&offset=20&limit=10", template
    ) == {"limit": "10", "offset": "20"}
    assert match_uri_template("search://items?fixed=10", template) is None
    assert match_uri_template("search://items?fixed=2&limit=10", template) is None


def test_match_literal_query_continuation_rejects_undeclared_keys():
    template = "search://items?fixed=1{&limit}"
    assert match_uri_template("search://items?fixed=1&view=full", template) is None
    assert (
        match_uri_template("search://items?fixed=1&limit=10&view=full", template)
        is None
    )
    assert (
        match_uri_template("search://items?fixed=1&view=full&limit=10", template)
        is None
    )
    assert match_uri_template("search://items?fixed=1&", template) is None
    assert match_uri_template("search://items?fixed=1&limit=10&", template) is None


def test_match_literal_query_continuation_before_literal_suffix_and_fragment():
    ordered = "search://ordered?fixed=1{&limit}&format=json"
    assert match_uri_template("search://ordered?fixed=1&format=json", ordered) == {}
    assert match_uri_template(
        "search://ordered?fixed=1&limit=10&format=json", ordered
    ) == {"limit": "10"}
    assert (
        expand_uri_template(ordered, {"limit": 10})
        == "search://ordered?fixed=1&limit=10&format=json"
    )
    assert expand_uri_template(ordered, {}) == "search://ordered?fixed=1&format=json"
    assert (
        match_uri_template("search://ordered?fixed=1&view=full&format=json", ordered)
        is None
    )
    assert (
        match_uri_template(
            "search://ordered?fixed=1&limit=10&view=full&format=json", ordered
        )
        is None
    )
    assert (
        match_uri_template("search://ordered?fixed=1&format=json&limit=10", ordered)
        is None
    )

    fragmented = "search://items?fixed=1{&limit}#summary"
    assert match_uri_template("search://items?fixed=1#summary", fragmented) == {}
    assert match_uri_template(
        "search://items?fixed=1&limit=10#summary", fragmented
    ) == {"limit": "10"}
    assert (
        expand_uri_template(fragmented, {"limit": 10})
        == "search://items?fixed=1&limit=10#summary"
    )
    assert expand_uri_template(fragmented, {}) == "search://items?fixed=1#summary"
    assert (
        match_uri_template("search://items?fixed=1&view=full#summary", fragmented)
        is None
    )
    assert (
        match_uri_template("search://items?fixed=1&limit=10#other", fragmented) is None
    )


def test_match_literal_query_multiple_continuations_and_fragment_stripping():
    template = "search://items?fixed=1{&limit}&format=json{&offset}#summary"
    assert match_uri_template(
        "search://items?fixed=1&limit=10&format=json&offset=20#summary", template
    ) == {"limit": "10", "offset": "20"}
    assert (
        match_uri_template("search://items?fixed=1&format=json#summary", template) == {}
    )
    assert (
        match_uri_template(
            "search://items?fixed=1&offset=20&format=json&limit=10#summary", template
        )
        is None
    )
    # Templates without a literal `#` continue to ignore client fragments.
    assert match_uri_template(
        "search://items?fixed=1&limit=10#ignored", "search://items?fixed=1{&limit}"
    ) == {"limit": "10"}
    # Literal fragments containing `?` after `#` continue to round-trip and match.
    frag_q = "test://items/{id}#section?view={view}"
    expanded_frag_q = expand_uri_template(frag_q, {"id": "42", "view": "full"})
    assert expanded_frag_q == "test://items/42#section?view=full"
    assert match_uri_template(expanded_frag_q, frag_q) == {
        "id": "42",
        "view": "full",
    }


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
    template = "search://items?fixed=1{&tags*}&format=json"
    assert match_uri_template(
        "search://items?fixed=1&tags=a%2Cb&tags=c%2Bd&tags=e%26f&format=json",
        template,
    ) == {"tags": ["a,b", "c+d", "e&f"]}


def test_literal_query_without_continuation_still_rejects_extra_params():
    template = "search://items?q={q}"
    assert match_uri_template("search://items?q=hello", template) == {"q": "hello"}
    assert match_uri_template("search://items?q=hello&extra=1", template) is None


def test_forward_uri_preserves_continuation_in_original_position():
    trailing = "search://items?q={q}{&limit}"
    assert (
        forward_uri(trailing, {"q": "hello"}, "search://sub/items?q=hello&limit=10")
        == "search://items?q=hello&limit=10"
    )
    assert (
        forward_uri(trailing, {"q": "hello"}, "search://sub/items?q=hello")
        == "search://items?q=hello"
    )

    ordered = "search://ordered?fixed=1{&tags*}&format=json"
    assert (
        forward_uri(
            ordered,
            {"tags": ["a,b", "c+d", "e&f"]},
            "search://sub/ordered?fixed=1&tags=a%2Cb&tags=c%2Bd&tags=e%26f&format=json",
        )
        == "search://ordered?fixed=1&tags=a%2Cb&tags=c%2Bd&tags=e%26f&format=json"
    )
    assert (
        forward_uri(ordered, {}, "search://sub/ordered?fixed=1&format=json")
        == "search://ordered?fixed=1&format=json"
    )

    fragmented = "search://items?fixed=1{&limit}#summary"
    assert (
        forward_uri(
            fragmented,
            {"limit": "10"},
            "search://sub/items?fixed=1&limit=10#summary",
        )
        == "search://items?fixed=1&limit=10#summary"
    )
    assert (
        forward_uri(fragmented, {}, "search://sub/items?fixed=1#summary")
        == "search://items?fixed=1#summary"
    )

    # Non-exploded comma-joined lists must retain raw %2C separators byte-for-byte.
    csv_list = "search://items?fixed=1{&tags}&format=json"
    assert (
        forward_uri(
            csv_list,
            {"tags": ["a,b", "c"]},
            "search://sub/items?fixed=1&tags=a%2Cb,c&format=json",
        )
        == "search://items?fixed=1&tags=a%2Cb,c&format=json"
    )


@pytest.mark.parametrize("via", ["direct", "mount", "proxy"])
async def test_client_reads_literal_query_continuation_templates(via: str):
    sub = FastMCP("sub")

    @sub.resource("search://items?fixed=1{&limit}")
    def search_fixed(limit: int = 5) -> str:
        return f"limit={limit}"

    @sub.resource("search://items?q={q}{&limit}")
    def search_q(q: str, limit: int = 5) -> str:
        return f"q={q} limit={limit}"

    @sub.resource("search://ordered?fixed=1{&limit}&format=json")
    def search_ordered(limit: int = 5) -> str:
        return f"ordered={limit}"

    @sub.resource("search://summary?fixed=1{&limit}#summary")
    def search_summary(limit: int = 5) -> str:
        return f"summary={limit}"

    @sub.resource("search://tags?fixed=1{&tags*}&format=json")
    def search_tags(tags: list[str] | None = None) -> str:
        return f"tags={tags!r}"

    prefix = ""
    if via == "direct":
        server = sub
    elif via == "mount":
        server = FastMCP("parent")
        server.mount(sub, "sub")
        prefix = "sub/"
    else:
        server = create_proxy(sub)

    async with Client(server) as client:
        res1 = await client.read_resource(f"search://{prefix}items?fixed=1&limit=10")
        assert res1[0].text == "limit=10"

        res2 = await client.read_resource(f"search://{prefix}items?q=hello&limit=10")
        assert res2[0].text == "q=hello limit=10"

        res3 = await client.read_resource(f"search://{prefix}items?q=hello")
        assert res3[0].text == "q=hello limit=5"

        # Undeclared keys must be rejected even when declared keys are present or omitted.
        for bad_uri in (
            f"search://{prefix}items?fixed=2&limit=10",
            f"search://{prefix}items?fixed=1&view=full",
            f"search://{prefix}items?fixed=1&limit=10&view=full",
            f"search://{prefix}ordered?fixed=1&view=full&format=json",
            f"search://{prefix}summary?fixed=1&view=full#summary",
        ):
            with pytest.raises(MCPError):
                await client.read_resource(bad_uri)

        # Mid-query continuation before trailing literal query parameter.
        ordered_with = await client.read_resource(
            f"search://{prefix}ordered?fixed=1&limit=10&format=json"
        )
        assert ordered_with[0].text == "ordered=10"
        ordered_omitted = await client.read_resource(
            f"search://{prefix}ordered?fixed=1&format=json"
        )
        assert ordered_omitted[0].text == "ordered=5"

        # Continuation before literal fragment.
        summary_with = await client.read_resource(
            f"search://{prefix}summary?fixed=1&limit=10#summary"
        )
        assert summary_with[0].text == "summary=10"
        summary_omitted = await client.read_resource(
            f"search://{prefix}summary?fixed=1#summary"
        )
        assert summary_omitted[0].text == "summary=5"

        # Exploded continuation with escaped commas, plus signs, and ampersands before suffix.
        tags_res = await client.read_resource(
            f"search://{prefix}tags?fixed=1&tags=a%2Cb&tags=c%2Bd&tags=e%26f&format=json"
        )
        assert tags_res[0].text == "tags=['a,b', 'c+d', 'e&f']"
