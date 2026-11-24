"""Each OpenAPI parameter reaches the backend as the value the tool was called with.

One drawn operation carries path, header, cookie and object-valued query
parameters with drawn `style`/`explode`; `FastMCP.from_openapi` serves it over
a mock transport that records the request. A decoder written from the OpenAPI
3.1 serialization rules (and docs/integrations/openapi.mdx where fastmcp
picks among them) reads each parameter back:

- path, `simple` style: arrays join with `,`; objects are `k,v,...`, or
  `k=v,...` when exploded; each item is percent-encoded on its own, so the
  separators stay literal and an item may hold any character
- header, `simple` style: the same text, without percent-encoding (header
  values cannot hold the separators unambiguously, so items here do not)
- cookie, `form` style (explode defaults to true): an exploded array repeats
  the name and an exploded object sends one pair per property; non-exploded
  forms join with `,` and percent-encode `%`, `,`, `;` and `=` inside items.
  A cookie pair is `name=value` with no `;` in either, or it reads as more
  pairs than were sent
- query objects: `form` with explode sends one pair per property, without
  explode `name=k,v,...`; `deepObject` sends `name[k]=v`
- an empty array or object is left out
"""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx2
import pytest
from hypothesis import assume, event, given, note
from hypothesis import strategies as st

from fastmcp import Client, FastMCP

from .known import still_open

LOOP = asyncio.new_event_loop()
# Every character a separator or encoder could trip over, plus plain ones.
ANY_TEXT = st.text(st.sampled_from("ab ,;=%&|.ü/"), min_size=1, max_size=4)
SEPARATORS = st.text(st.sampled_from("a,;=%"), min_size=1, max_size=4)
PLAIN_TEXT = st.text(st.sampled_from("ab-_"), min_size=1, max_size=4)
KEYS = ["r", "g", "b"]


def text(v: Any) -> str:
    return json.dumps(v) if isinstance(v, bool) else str(v)


def scalars(strings: st.SearchStrategy[str]) -> st.SearchStrategy[Any]:
    return strings | st.integers(-3, 300) | st.booleans()


@st.composite
def parameters(draw) -> tuple[dict[str, Any], Any]:
    """One parameter object and a value to call it with."""
    where = draw(st.sampled_from(["path", "header", "cookie", "query"]))
    shape = draw(
        st.sampled_from(
            ["object"] if where == "query" else ["scalar", "array", "object"]
        )
    )
    param: dict[str, Any] = {"in": where, "required": True}
    if where == "query":
        param["style"] = draw(st.sampled_from(["form", "deepObject"]))
    # Cookies lean towards the joined (explode=false) forms, which encode.
    explode = draw(
        st.sampled_from(
            [None, True, False, False] if where == "cookie" else [None, True, False]
        )
    )
    if param.get("style") == "deepObject":
        param["explode"] = True
    elif explode is not None:
        param["explode"] = explode

    strings = PLAIN_TEXT if where == "header" else ANY_TEXT
    if where == "cookie":
        # Separators inside cookie items are the interesting case. While #6694
        # is open, an item sent raw (a scalar, or exploded) avoids its shape:
        # skipping whole examples would starve the joined forms.
        strings = st.one_of(SEPARATORS, SEPARATORS, ANY_TEXT)
        if ISSUE_6694_OPEN and (shape == "scalar" or explode_of(param)):
            # `%` too: sent raw, it reads back as an escape it never was.
            strings = strings.filter(
                lambda v: ";" not in v and "%" not in v and v.isascii()
            )
    item = {"type": "string"}
    if shape == "scalar":
        value = draw(scalars(strings))
        schema = {"type": {str: "string", int: "integer", bool: "boolean"}[type(value)]}
    elif shape == "array":
        value = draw(st.lists(strings, max_size=3))
        schema = {"type": "array", "items": item}
    else:
        keys = draw(st.lists(st.sampled_from(KEYS), max_size=3, unique=True))
        value = {k: draw(strings) for k in keys}
        schema = {"type": "object", "properties": {k: item for k in KEYS}}
    if where == "cookie" and ISSUE_6694_OPEN:
        # A joined cookie still cannot hold non-ASCII (#6694).
        items = [*value.values()] if isinstance(value, dict) else value
        assume(
            all(
                str(v).isascii()
                for v in (items if isinstance(items, list) else [items])
            )
        )
    if where == "path":
        # Dot segments are refused on purpose (path traversal), not encoded.
        items = [*value, *value.values()] if isinstance(value, dict) else value
        for v in items if isinstance(items, list) else [items]:
            assume(not ({".", ".."} & set(str(v).replace("\\", "/").split("/"))))
    param["schema"] = schema
    return param, value


def explode_of(p: dict[str, Any]) -> bool:
    default = p["in"] in {"cookie", "query"}
    return p.get("explode", default)


def _split_simple(raw: str, kind: str, explode: bool, decode) -> Any:
    if kind == "scalar":
        return decode(raw)
    items = raw.split(",")
    if kind == "array":
        return [decode(i) for i in items]
    if explode:
        return dict((decode(k), decode(v)) for k, v in (i.split("=", 1) for i in items))
    return {decode(items[i]): decode(items[i + 1]) for i in range(0, len(items), 2)}


def kind_of(value: Any) -> str:
    return (
        "array"
        if isinstance(value, list)
        else "object"
        if isinstance(value, dict)
        else "scalar"
    )


def as_text(value: Any) -> Any:
    if isinstance(value, list):
        return [text(v) for v in value]
    if isinstance(value, dict):
        return {k: text(v) for k, v in value.items()}
    return text(value)


def cookie_pairs(header: str | None) -> list[tuple[str, str]]:
    if not header:
        return []
    # RFC 6265 section 5.4: pairs split on ";", optional whitespace after it.
    pairs = [part.lstrip(" ").partition("=") for part in header.split(";")]
    return [(name, value) for name, _, value in pairs]


def _issue_6694_probe() -> bool:
    """A scalar or exploded cookie value holding `;` is sent raw, so the
    backend reads cookies the call never set.
    https://github.com/PrefectHQ/fastmcp/issues/6694"""
    spec = _spec(
        [{"name": "c", "in": "cookie", "required": True, "schema": {"type": "string"}}]
    )
    req = _send(spec, {"c": "x; admin=1"})
    pairs = cookie_pairs(req.headers.get("cookie"))
    # Fixed means one cookie `c` that decodes to the value, however encoded.
    return [(n, unquote(v)) for n, v in pairs] != [("c", "x; admin=1")]


def _spec(params: list[dict[str, Any]]) -> dict[str, Any]:
    path = "/items" + "".join(f"/{{{p['name']}}}" for p in params if p["in"] == "path")
    op = {
        "operationId": "op",
        "parameters": params,
        "responses": {
            "200": {
                "description": "ok",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": {path: {"get": op}},
    }


def _send(spec: dict[str, Any], args: dict[str, Any]) -> httpx2.Request:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={})

    async def run():
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), base_url="http://api"
        ) as client:
            server = FastMCP.from_openapi(openapi_spec=spec, client=client)
            async with Client(server) as mcp:
                result = await mcp.call_tool("op", args, raise_on_error=False)
                assert not result.is_error, f"a valid call failed: {result.content}"

    LOOP.run_until_complete(run())
    assert len(requests) == 1, "the call did not send exactly one request"
    return requests[0]


ISSUE_6694_OPEN = still_open("#6694", _issue_6694_probe)


@pytest.fixture(autouse=True)
def _quiet():
    logger = logging.getLogger("fastmcp")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    yield
    logger.setLevel(level)


@pytest.fixture(autouse=True, scope="module")
def _close_loop():
    yield
    LOOP.close()


@given(drawn=st.lists(parameters(), min_size=1, max_size=3))
def test_each_parameter_reaches_the_backend_as_sent(drawn):
    params, args = [], {}
    for i, (p, value) in enumerate(drawn):
        p = {"name": f"p{i}", **p}
        params.append(p)
        args[p["name"]] = value
    spec = _spec(params)
    note(f"params={json.dumps(params)}")
    note(f"args={args}")
    req = _send(spec, args)
    note(f"url={req.url} cookie={req.headers.get('cookie')}")

    raw = urlsplit(str(req.url))
    segments = raw.path.split("/")[2:]  # after "", "items"
    path_params = [p for p in params if p["in"] == "path"]
    query = parse_qsl(raw.query, keep_blank_values=True)
    cookies = cookie_pairs(req.headers.get("cookie"))
    claimed_query: set[int] = set()
    claimed_cookies: set[int] = set()

    for p in params:
        name, value = p["name"], args[p["name"]]
        kind, explode, want = kind_of(value), explode_of(p), as_text(value)
        empty = kind != "scalar" and not value
        event(f"{p['in']} {kind}{' exploded' if explode else ''}")

        if p["in"] == "path":
            seg = segments[path_params.index(p)]
            if empty:
                assert seg == "", f"path {name}: empty {kind} sent as {seg!r}"
                continue
            got = _split_simple(seg, kind, explode, unquote)
            assert got == want, f"path {name}: {seg!r} reads as {got!r}"

        elif p["in"] == "header":
            raw_header = req.headers.get(name, "")
            if empty:
                continue  # an empty header value carries nothing to decode
            got = _split_simple(raw_header, kind, explode, lambda s: s)
            assert got == want, f"header {name}: {raw_header!r} reads as {got!r}"

        elif p["in"] == "cookie":
            if empty:
                assert all(n != name for n, _ in cookies), f"empty cookie {name} sent"
                continue
            if kind == "object" and explode:
                mine = []
                for key in value:
                    i = next(
                        (
                            i
                            for i, c in enumerate(cookies)
                            if c[0] == key
                            and i not in claimed_cookies
                            and i not in {j for j, _ in mine}
                        ),
                        None,
                    )
                    if i is not None:
                        mine.append((i, cookies[i]))
                got: Any = {n: unquote(v) for _, (n, v) in mine}
            elif kind == "array" and explode:
                mine = [
                    (i, c)
                    for i, c in enumerate(cookies)
                    if c[0] == name and i not in claimed_cookies
                ]
                got = [unquote(v) for _, (n, v) in mine]
            else:
                mine = [
                    (i, c)
                    for i, c in enumerate(cookies)
                    if c[0] == name and i not in claimed_cookies
                ][:1]
                assert mine, f"cookie {name} missing from {cookies}"
                v = mine[0][1][1]
                got = _split_simple(v, kind, False, unquote)
            claimed_cookies |= {i for i, _ in mine}
            assert got == want, f"cookie {name}: {cookies} reads as {got!r}"

        else:  # query object
            if empty:
                assert all(not k.startswith(name) for k, _ in query), (
                    f"empty {name} sent"
                )
                continue
            if p["style"] == "deepObject":
                mine = [
                    (i, kv)
                    for i, kv in enumerate(query)
                    if kv[0].startswith(f"{name}[")
                ]
                got = {k[len(name) + 1 : -1]: v for _, (k, v) in mine}
            elif explode:
                # Bare property names may repeat across parameters (the spec
                # cannot tell them apart); take one unclaimed pair per key.
                mine = []
                for key in value:
                    i = next(
                        (
                            i
                            for i, kv in enumerate(query)
                            if kv[0] == key
                            and i not in claimed_query
                            and i not in {j for j, _ in mine}
                        ),
                        None,
                    )
                    if i is not None:
                        mine.append((i, query[i]))
                got = {k: v for _, (k, v) in mine}
            else:
                mine = [(i, kv) for i, kv in enumerate(query) if kv[0] == name]
                assert len(mine) == 1, f"query {name}: {query}"
                joined = mine[0][1][1]
                if "," in "".join([*value, *value.values()]):
                    # A comma inside an item reads as a separator once the
                    # value is decoded; check the text instead of splitting.
                    event("query object: comma inside an item")
                    pairs = [t for kv in want.items() for t in kv]
                    assert joined == ",".join(pairs), f"query {name}: {joined!r}"
                    got = want
                else:
                    got = _split_simple(joined, kind, False, lambda s: s)
            claimed_query |= {i for i, _ in mine}
            assert got == want, f"query {name}: {query} reads as {got!r}"

    assert len(claimed_query) == len(query), f"query pairs never sent: {query}"
    assert len(claimed_cookies) == len(cookies), (
        f"cookies the call never set: {cookies}"
    )
