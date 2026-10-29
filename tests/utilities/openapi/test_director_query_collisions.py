"""Regression tests for colliding OpenAPI query parameter names."""

import pytest
from jsonschema_path import SchemaPath

from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import HTTPRoute, ParameterInfo


@pytest.mark.parametrize(
    "query_values",
    [
        {"filter": {"q": 1}, "q": False, "category": "books"},
        {"q": False, "filter": {"q": 1}, "category": "books"},
    ],
)
@pytest.mark.parametrize(("style", "explode"), [(None, None), ("form", True)])
def test_exploded_object_preserves_overlapping_query_parameter(
    query_values, style, explode
):
    route = HTTPRoute(
        path="/items",
        method="GET",
        operation_id="list_items",
        parameters=[
            ParameterInfo(
                name="filter",
                location="query",
                required=False,
                schema={"type": "object"},
                style=style,
                explode=explode,
            ),
            ParameterInfo(
                name="q", location="query", required=False, schema={"type": "boolean"}
            ),
            ParameterInfo(
                name="category",
                location="query",
                required=False,
                schema={"type": "string"},
            ),
        ],
    )
    spec = SchemaPath.from_dict(
        {"openapi": "3.0.0", "info": {"title": "test", "version": "1"}, "paths": {}}
    )
    request = RequestDirector(spec).build(route, query_values, "https://example.com")

    assert request.url.params["category"] == "books"
    assert sorted(request.url.params.get_list("q")) == ["1", "false"]
