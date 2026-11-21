"""An OpenAPI tool sends the request its spec describes and returns the response.

A drawn OpenAPI 3.1 spec (operations with path, query and header parameters,
JSON bodies, and names that collide across locations) becomes a server through
`FastMCP.from_openapi`, backed by a mock HTTP transport that records each
request and answers with a drawn response. The model follows
docs/integrations/openapi.mdx and the OpenAPI 3.1 parameter serialization rules:

- one tool per operation, named by its `operationId`
- a parameter whose name is also used in another location is listed as
  `name__<location>`; body properties keep their names
- the arguments the spec marks required are required in the tool's schema
- a call sends the operation's method and path, every path value
  percent-encoded so the backend decodes it back to the value sent, query
  values serialized by `style`/`explode` (booleans as `true`/`false`),
  omitted optional parameters left out, header values as strings, and the body
  properties as JSON
- the tool's structured result is the JSON the backend answered with (wrapped
  in `result` when it is not an object)
"""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx2
import pytest
from hypothesis import event, given, note
from hypothesis import strategies as st

from fastmcp import Client, FastMCP

METHODS = ["get", "post", "put", "patch", "delete"]
NAMES = ["id", "q", "tags", "flag", "limit"]
STRINGS = ["", "a", "a b", "a/b", "ü", "x%y", "?&=", "+", "report.json"]
LOOP = asyncio.new_event_loop()


def scalar_schemas():
    return st.sampled_from(
        [{"type": "string"}, {"type": "integer"}, {"type": "boolean"}]
    )


def values_for(schema: dict[str, Any], *, in_path: bool = False, ascii: bool = False):
    kind = schema.get("type")
    if kind == "integer":
        return st.integers(-3, 1000)
    if kind == "boolean":
        return st.booleans()
    if kind == "array":
        return st.lists(
            values_for(schema["items"], in_path=in_path), min_size=1, max_size=3
        )
    if kind == "object":
        return st.fixed_dictionaries(
            {k: values_for(v) for k, v in schema.get("properties", {}).items()}
        )
    pool = [s for s in STRINGS if s] if in_path else STRINGS
    if ascii:
        # HTTP header values are ASCII; httpx refuses anything else before
        # sending, which the tool reports as an error. Not a fastmcp choice.
        pool = [s for s in pool if s.isascii()]
    return st.sampled_from(pool)


@st.composite
def operations(draw: st.DrawFn, index: int) -> tuple[str, str, dict[str, Any]]:
    method = draw(st.sampled_from(METHODS))
    params: list[dict[str, Any]] = []
    path = f"/r{index}"
    for name in draw(st.lists(st.sampled_from(NAMES), max_size=2, unique=True)):
        path += f"/{{{name}}}"
        schema = draw(scalar_schemas())
        params.append({"name": name, "in": "path", "required": True, "schema": schema})
    for name in draw(st.lists(st.sampled_from(NAMES), max_size=3, unique=True)):
        if draw(st.booleans()):
            schema = {"type": "array", "items": draw(scalar_schemas())}
            param = {"name": name, "in": "query", "schema": schema}
            style = draw(
                st.sampled_from([None, "form", "pipeDelimited", "spaceDelimited"])
            )
            if style:
                param["style"] = style
            explode = draw(st.sampled_from([None, True, False]))
            if explode is not None:
                param["explode"] = explode
        else:
            param = {"name": name, "in": "query", "schema": draw(scalar_schemas())}
        param["required"] = draw(st.booleans())
        params.append(param)
    if draw(st.booleans()):
        params.append(
            {
                "name": draw(st.sampled_from(["X-Trace", "id"])),
                "in": "header",
                "required": draw(st.booleans()),
                "schema": draw(scalar_schemas()),
            }
        )
    op: dict[str, Any] = {"operationId": f"op{index}", "parameters": params}
    if method in ("post", "put", "patch") and draw(st.booleans()):
        props = {
            n: draw(scalar_schemas())
            for n in draw(
                st.lists(st.sampled_from(NAMES), min_size=1, max_size=3, unique=True)
            )
        }
        required = sorted(draw(st.sets(st.sampled_from(sorted(props)))))
        body = {"type": "object", "properties": props}
        if required:
            body["required"] = required
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": body}},
        }
    response = draw(
        st.sampled_from(
            [
                {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                {"type": "array", "items": {"type": "integer"}},
                {"type": "string"},
                {"type": "integer"},
            ]
        )
    )
    op["responses"] = {
        "200": {
            "description": "ok",
            "content": {"application/json": {"schema": response}},
        }
    }
    return method, path, op


def argument_names(op: dict[str, Any]) -> dict[tuple[str, str], str]:
    """(location, name) -> tool argument name, per the documented suffix rule."""
    body = (
        op.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
    )
    places: dict[str, int] = {}
    for p in op["parameters"]:
        places[p["name"]] = places.get(p["name"], 0) + 1
    for name in body:
        places[name] = places.get(name, 0) + 1
    out = {}
    for p in op["parameters"]:
        clash = places[p["name"]] > 1
        out[(p["in"], p["name"])] = f"{p['name']}__{p['in']}" if clash else p["name"]
    for name in body:
        out[("body", name)] = name
    return out


def query_wire(param: dict[str, Any], value: Any) -> list[tuple[str, str]]:
    """The (name, decoded value) pairs OpenAPI 3.1 puts in the query string."""

    def text(v: Any) -> str:
        return json.dumps(v) if isinstance(v, bool) else str(v)

    name = param["name"]
    if not isinstance(value, list):
        return [(name, text(value))]
    style = param.get("style", "form")
    explode = param.get("explode", style == "form")
    if explode:
        return [(name, text(v)) for v in value]
    sep = {"form": ",", "pipeDelimited": "|", "spaceDelimited": " "}[style]
    return [(name, sep.join(text(v) for v in value))]


@pytest.fixture(autouse=True)
def _quiet():
    logger = logging.getLogger("fastmcp")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    yield
    logger.setLevel(level)


@given(data=st.data())
def test_openapi_tool_sends_described_request(data):
    count = data.draw(st.integers(1, 2), label="operations")
    paths: dict[str, Any] = {}
    ops: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for i in range(count):
        method, path, op = data.draw(operations(i), label=f"operation {i}")
        paths.setdefault(path, {})[method] = op
        ops[op["operationId"]] = (method, path, op)
    spec = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": paths}

    target = data.draw(st.sampled_from(sorted(ops)), label="call")
    method, path, op = ops[target]
    names = argument_names(op)
    body_schema = (
        op.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    body_required = set(body_schema.get("required", [])) if body_schema else set()

    # Arguments: every required one, each optional one sometimes.
    args: dict[str, Any] = {}
    sent: dict[tuple[str, str], Any] = {}
    for p in op["parameters"]:
        if p["required"] or data.draw(st.booleans(), label=f"send {p['name']}"):
            v = data.draw(
                values_for(
                    p["schema"], in_path=p["in"] == "path", ascii=p["in"] == "header"
                )
            )
            args[names[(p["in"], p["name"])]] = v
            sent[(p["in"], p["name"])] = v
    for name, schema in (body_schema or {}).get("properties", {}).items():
        if name in body_required or data.draw(st.booleans(), label=f"send {name}"):
            v = data.draw(values_for(schema))
            args[name] = v
            sent[("body", name)] = v
    response_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    answer = data.draw(values_for(response_schema), label="response")
    note(f"spec={json.dumps(spec)}")
    note(f"args={args}")

    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=answer)

    async def run():
        client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), base_url="http://api"
        )
        server = FastMCP.from_openapi(openapi_spec=spec, client=client)
        async with Client(server) as mcp:
            listed = {t.name: t for t in await mcp.list_tools()}
            result = await mcp.call_tool(target, args, raise_on_error=False)
            return listed, result

    listed, result = LOOP.run_until_complete(run())

    assert set(listed) == set(ops), "tools differ from the spec's operations"
    schema = listed[target].input_schema
    assert set(schema.get("properties", {})) == set(names.values()), (
        "the tool's arguments differ from the documented names"
    )
    want_required = {
        names[(p["in"], p["name"])] for p in op["parameters"] if p["required"]
    }
    want_required |= body_required
    assert set(schema.get("required", [])) == want_required, (
        "the tool's required arguments differ from the spec"
    )

    assert not result.is_error, f"a valid call failed: {result.content}"
    assert len(requests) == 1, "the call did not send exactly one request"
    req = requests[0]
    event(f"method={method}")
    assert req.method == method.upper()

    # Path: each segment decodes back to the value sent.
    raw = urlsplit(str(req.url))
    want_segments = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{"):
            v = sent[("path", seg[1:-1])]
            want_segments.append(json.dumps(v) if isinstance(v, bool) else str(v))
        else:
            want_segments.append(seg)
    got_segments = [unquote(s) for s in raw.path.strip("/").split("/")]
    assert got_segments == want_segments, (
        "a path value did not reach the backend intact"
    )

    # Query: the decoded pairs the spec's serialization rules give.
    want_query = sorted(
        pair
        for p in op["parameters"]
        if p["in"] == "query" and ("query", p["name"]) in sent
        for pair in query_wire(p, sent[("query", p["name"])])
    )
    got_query = sorted(parse_qsl(raw.query, keep_blank_values=True))
    assert got_query == want_query, "the query string differs from the spec's rules"

    for p in op["parameters"]:
        if p["in"] == "header" and ("header", p["name"]) in sent:
            v = sent[("header", p["name"])]
            want = json.dumps(v) if isinstance(v, bool) else str(v)
            assert req.headers.get(p["name"]) == want, "a header value differs"

    if body_schema is not None:
        want_body = {n: v for (loc, n), v in sent.items() if loc == "body"}
        got_body = json.loads(req.content) if req.content else {}
        assert got_body == want_body, "the JSON body differs from the arguments"

    want_result = answer if isinstance(answer, dict) else {"result": answer}
    assert result.structured_content == want_result, (
        "the result differs from the response"
    )
