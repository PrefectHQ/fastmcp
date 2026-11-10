"""Exploded form objects share the query namespace with other parameters.

OpenAPI treats a route's form-style query parameters as one RFC 6570 variable
list (`{?filter*,q}`), so an exploded object property named like another
parameter sends both values rather than one overwriting the other.
"""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP


def make_spec(q_parameter: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Query collisions", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "schema": {
                                "type": "object",
                                "properties": {"q": {"type": "integer"}},
                            },
                        },
                        {"name": "q", "in": "query", **q_parameter},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


async def sent_query(spec: dict[str, Any], args: dict[str, Any]) -> list[tuple]:
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={})

    async with httpx2.AsyncClient(
        base_url="https://example.test", transport=httpx2.MockTransport(capture)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("list_items", args)

    assert len(requests) == 1
    return requests[0].url.params.multi_items()


@pytest.mark.parametrize(
    "q_parameter,q_value,q_items",
    [
        ({"schema": {"type": "boolean"}}, False, [("q", "false")]),
        (
            {"schema": {"type": "array", "items": {"type": "integer"}}},
            [2, 3],
            [("q", "2"), ("q", "3")],
        ),
        (
            {
                "explode": False,
                "schema": {"type": "array", "items": {"type": "integer"}},
            },
            [2, 3],
            [("q", "2,3")],
        ),
    ],
)
@pytest.mark.parametrize("filter_first", [True, False])
async def test_exploded_object_property_and_parameter_both_send(
    q_parameter: dict[str, Any],
    q_value: Any,
    q_items: list[tuple[str, str]],
    filter_first: bool,
) -> None:
    spec = make_spec(q_parameter)
    filter_arg = {"filter": {"q": 1}}
    q_arg = {"q": q_value}
    args = {**filter_arg, **q_arg} if filter_first else {**q_arg, **filter_arg}

    sent = await sent_query(spec, args)

    expected = [("q", "1"), *q_items] if filter_first else [*q_items, ("q", "1")]
    assert sent == expected


async def test_non_colliding_object_properties_are_unchanged() -> None:
    spec = make_spec({"schema": {"type": "boolean"}})

    sent = await sent_query(spec, {"filter": {"r": 1}, "q": True})

    assert sent == [("r", "1"), ("q", "true")]
