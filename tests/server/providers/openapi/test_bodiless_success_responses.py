"""Operations that may answer with a success response that has no body."""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP

INVITATION = {"id": 7, "invitee": "octocat"}

JSON_INVITATION = {
    "description": "Invitation created",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "invitee": {"type": "string"},
                },
            }
        }
    },
}
NO_CONTENT = {"description": "The user already has access"}


def spec_with_responses(responses: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Collaborators", "version": "1.0"},
        "paths": {
            "/collaborators/{username}": {
                "put": {
                    "operationId": "add_collaborator",
                    "parameters": [
                        {
                            "name": "username",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": responses,
                }
            }
        },
        "components": {"responses": {"NoContent": NO_CONTENT}},
    }


def respond(request: httpx2.Request) -> httpx2.Response:
    if request.url.path.endswith("/existing"):
        return httpx2.Response(204)
    return httpx2.Response(201, json=INVITATION)


def server_for(responses: dict[str, Any], *, validate_output: bool = True) -> FastMCP:
    upstream = httpx2.AsyncClient(
        base_url="https://api.example.test",
        transport=httpx2.MockTransport(respond),
    )
    return FastMCP.from_openapi(
        openapi_spec=spec_with_responses(responses),
        client=upstream,
        validate_output=validate_output,
    )


BODILESS_SIBLINGS = [
    pytest.param({"201": JSON_INVITATION, "204": NO_CONTENT}, id="201-and-204"),
    pytest.param({"200": JSON_INVITATION, "202": NO_CONTENT}, id="200-and-202"),
    pytest.param({"204": NO_CONTENT, "201": JSON_INVITATION}, id="204-listed-first"),
    pytest.param(
        {"201": JSON_INVITATION, "204": {"$ref": "#/components/responses/NoContent"}},
        id="referenced-204",
    ),
    pytest.param(
        {"201": JSON_INVITATION, "204": {"description": "None", "content": {}}},
        id="empty-content",
    ),
]


@pytest.mark.parametrize("responses", BODILESS_SIBLINGS)
@pytest.mark.parametrize("validate_output", [True, False])
async def test_bodiless_success_response_drops_output_schema(
    responses: dict[str, Any], validate_output: bool
):
    server = server_for(responses, validate_output=validate_output)

    async with Client(server) as client:
        (tool,) = await client.list_tools()

    assert tool.output_schema is None


async def test_bodiless_success_call_succeeds_next_to_json_success():
    server = server_for({"201": JSON_INVITATION, "204": NO_CONTENT})

    async with Client(server) as client:
        created = await client.call_tool("add_collaborator", {"username": "new"})
        existing = await client.call_tool("add_collaborator", {"username": "existing"})

    assert created.is_error is False
    assert created.structured_content == INVITATION
    assert existing.is_error is False
    assert existing.structured_content is None


@pytest.mark.parametrize(
    "responses",
    [
        pytest.param({"200": JSON_INVITATION}, id="json-only"),
        pytest.param(
            {"201": JSON_INVITATION, "404": {"description": "Not found"}},
            id="bodiless-error",
        ),
        pytest.param(
            {"201": JSON_INVITATION, "default": {"description": "Unexpected error"}},
            id="bodiless-default",
        ),
        pytest.param(
            {"200": JSON_INVITATION, "201": JSON_INVITATION}, id="two-json-successes"
        ),
    ],
)
async def test_output_schema_kept_without_bodiless_success_response(
    responses: dict[str, Any],
):
    server = server_for(responses)

    async with Client(server) as client:
        (tool,) = await client.list_tools()

    assert tool.output_schema is not None
    assert tool.output_schema["properties"]["invitee"] == {"type": "string"}
