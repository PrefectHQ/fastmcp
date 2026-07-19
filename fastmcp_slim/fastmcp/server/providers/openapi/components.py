"""OpenAPI component classes: Tool, Resource, and ResourceTemplate."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

import httpx2
from mcp_types import ToolAnnotations
from pydantic.networks import AnyUrl

from fastmcp.resources import (
    Resource,
    ResourceContent,
    ResourceResult,
    ResourceTemplate,
)
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.exceptions import (
    HTTP_STATUS_ERRORS,
    REQUEST_ERRORS,
    TIMEOUT_ERRORS,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import HTTPRoute
from fastmcp.utilities.openapi.director import RequestDirector

if TYPE_CHECKING:
    from fastmcp.server import Context

_SAFE_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "host",
        "user-agent",
    }
)


def _redact_headers(headers: httpx2.Headers) -> dict[str, str]:
    return {k: v if k.lower() in _SAFE_HEADERS else "***" for k, v in headers.items()}


__all__ = [
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
    "_extract_mime_type_from_route",
]

logger = get_logger(__name__)

# Default MIME type when no response content type can be inferred
_DEFAULT_MIME_TYPE = "application/json"


def _extract_mime_type_from_route(route: HTTPRoute) -> str:
    """Extract the primary MIME type from an HTTPRoute's response definitions.

    Looks for the first successful response (2xx) and returns its content type.
    Prefers JSON-compatible types when multiple are available.
    Falls back to "application/json" when no response content type is declared.
    """
    if not route.responses:
        return _DEFAULT_MIME_TYPE

    # Priority order for success status codes
    success_codes = ["200", "201", "202", "204"]

    response_info = None
    for status_code in success_codes:
        if status_code in route.responses:
            response_info = route.responses[status_code]
            break

    # If no explicit success codes, try any 2xx response
    if response_info is None:
        for status_code, resp_info in route.responses.items():
            if status_code.startswith("2"):
                response_info = resp_info
                break

    if response_info is None or not response_info.content_schema:
        return _DEFAULT_MIME_TYPE

    # If there's only one content type, use it directly
    content_types = list(response_info.content_schema.keys())
    if len(content_types) == 1:
        return content_types[0]

    # When multiple types exist, prefer JSON-compatible types
    json_compatible_types = [
        "application/json",
        "application/vnd.api+json",
        "application/hal+json",
        "application/ld+json",
        "text/json",
    ]
    for ct in json_compatible_types:
        if ct in response_info.content_schema:
            return ct

    # Fall back to the first available content type
    return content_types[0]


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug format.

    Only contains lowercase letters, uppercase letters, numbers, and underscores.
    """
    if not text:
        return ""

    # Replace spaces and common separators with underscores
    slug = re.sub(r"[\s\-\.]+", "_", text)

    # Remove non-alphanumeric characters except underscores
    slug = re.sub(r"[^a-zA-Z0-9_]", "", slug)

    # Remove multiple consecutive underscores
    slug = re.sub(r"_+", "_", slug)

    # Remove leading/trailing underscores
    slug = slug.strip("_")

    return slug


class OpenAPITool(Tool):
    """Tool implementation for OpenAPI endpoints."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(
        self,
        client: httpx2.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        name: str,
        description: str,
        parameters: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
    ):
        super().__init__(
            name=name,
            description=description,
            parameters=parameters,
            output_schema=output_schema,
            tags=tags or set(),
            annotations=annotations,
        )
        self._client = client
        self._route = route
        self._director = director

    def __repr__(self) -> str:
        return f"OpenAPITool(name={self.name!r}, method={self._route.method}, path={self._route.path})"

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the HTTP request using RequestDirector."""
        # Build the request — errors here are programming/schema issues,
        # not HTTP failures, so we catch them separately.
        try:
            base_url = str(self._client.base_url) or "http://localhost"
            directed_request = self._director.build(self._route, arguments, base_url)

            # Rebuild through the user's client so the request object comes
            # from whichever httpx library the client belongs to (a legacy
            # httpx.AsyncClient cannot send an httpx2.Request). Primitive
            # values (str/bytes/tuples) cross that boundary safely; client
            # default headers merge in with directed headers taking priority,
            # matching the previous manual merge.
            request = self._client.build_request(
                method=directed_request.method,
                url=str(directed_request.url.copy_with(query=None)),
                params=list(directed_request.url.params.multi_items()),
                headers=list(directed_request.headers.raw),
                # read() materializes streaming bodies (multipart files=)
                # that .content would refuse with RequestNotRead; idempotent
                # for plain byte bodies.
                content=directed_request.read(),
            )

            mcp_headers = get_http_headers()
            if mcp_headers:
                for key, value in mcp_headers.items():
                    if key not in request.headers:
                        request.headers[key] = value
        except Exception as e:
            raise ValueError(
                f"Error building request for {self._route.method.upper()} "
                f"{self._route.path}: {type(e).__name__}: {e}"
            ) from e

        # Send the request and process the response.
        try:
            logger.debug(
                f"run - sending request; headers: {_redact_headers(request.headers)}"
            )

            response = await self._client.send(request)
            response.raise_for_status()

            # Try to parse as JSON first
            try:
                result = response.json()

                # Handle structured content based on output schema
                if self.output_schema is not None:
                    if self.output_schema.get("x-fastmcp-wrap-result"):
                        structured_output = {"result": result}
                    else:
                        structured_output = result
                elif not isinstance(result, dict):
                    structured_output = {"result": result}
                else:
                    structured_output = result

                # Structured content must be a dict for the MCP protocol.
                # Wrap non-dict values that slipped through (e.g. a backend
                # returning an array when the schema declared an object).
                if not isinstance(structured_output, dict):
                    structured_output = {"result": structured_output}

                return ToolResult(structured_content=structured_output)
            except json.JSONDecodeError:
                return ToolResult(content=response.text)

        except HTTP_STATUS_ERRORS as e:
            status_error = cast("httpx2.HTTPStatusError", e)
            error_message = (
                f"HTTP error {status_error.response.status_code}: "
                f"{status_error.response.reason_phrase}"
            )
            try:
                error_data = status_error.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if status_error.response.text:
                    error_message += f" - {status_error.response.text}"
            raise ValueError(error_message) from e

        except TIMEOUT_ERRORS as e:
            raise ValueError(f"HTTP request timed out ({type(e).__name__})") from e

        except REQUEST_ERRORS as e:
            raise ValueError(f"Request error ({type(e).__name__}): {e!s}") from e


class OpenAPIResource(Resource):
    """Resource implementation for OpenAPI endpoints."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(
        self,
        client: httpx2.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "application/json",
        tags: set[str] | None = None,
        arguments: dict[str, Any] | None = None,
    ):
        super().__init__(
            uri=AnyUrl(uri),
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags or set(),
        )
        self._client = client
        self._route = route
        self._director = director
        self._arguments = dict(arguments or {})

    def __repr__(self) -> str:
        return f"OpenAPIResource(name={self.name!r}, uri={self.uri!r}, path={self._route.path})"

    async def read(self) -> ResourceResult:
        """Fetch the resource data by making an HTTP request."""
        try:
            base_url = str(self._client.base_url) or "http://localhost"
            directed_request = self._director.build(
                self._route, self._arguments, base_url
            )
            # Primitive values only: a legacy httpx.AsyncClient cannot accept
            # httpx2 URL/QueryParams/Headers objects.
            request = self._client.build_request(
                method=directed_request.method,
                url=str(directed_request.url.copy_with(query=None)),
                params=list(directed_request.url.params.multi_items()),
                headers=list(directed_request.headers.raw),
                # read() materializes streaming bodies (multipart files=)
                # that .content would refuse with RequestNotRead; idempotent
                # for plain byte bodies.
                content=directed_request.read(),
            )
            mcp_headers = get_http_headers()
            if mcp_headers:
                request.headers.update(mcp_headers)

            response = await self._client.send(request)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()

            if "application/json" in content_type:
                result = response.json()
                return ResourceResult(
                    contents=[
                        ResourceContent(
                            content=json.dumps(result), mime_type="application/json"
                        )
                    ]
                )
            elif any(ct in content_type for ct in ["text/", "application/xml"]):
                return ResourceResult(
                    contents=[
                        ResourceContent(content=response.text, mime_type=self.mime_type)
                    ]
                )
            else:
                return ResourceResult(
                    contents=[
                        ResourceContent(
                            content=response.content, mime_type=self.mime_type
                        )
                    ]
                )

        except HTTP_STATUS_ERRORS as e:
            status_error = cast("httpx2.HTTPStatusError", e)
            error_message = (
                f"HTTP error {status_error.response.status_code}: "
                f"{status_error.response.reason_phrase}"
            )
            try:
                error_data = status_error.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if status_error.response.text:
                    error_message += f" - {status_error.response.text}"
            raise ValueError(error_message) from e

        except TIMEOUT_ERRORS as e:
            raise ValueError(f"HTTP request timed out ({type(e).__name__})") from e

        except REQUEST_ERRORS as e:
            raise ValueError(f"Request error ({type(e).__name__}): {e!s}") from e


def _path_argument_name(route: HTTPRoute, parameter_name: str) -> str:
    for argument_name, mapping in route.parameter_map.items():
        if mapping["location"] == "path" and mapping["openapi_name"] == parameter_name:
            return argument_name
    return parameter_name


class OpenAPIResourceTemplate(ResourceTemplate):
    """Resource template implementation for OpenAPI endpoints."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(
        self,
        client: httpx2.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        uri_template: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        tags: set[str] | None = None,
        mime_type: str = _DEFAULT_MIME_TYPE,
    ):
        super().__init__(
            uri_template=uri_template,
            name=name,
            description=description,
            parameters=parameters,
            tags=tags or set(),
            mime_type=mime_type,
        )
        self._client = client
        self._route = route
        self._director = director

    def __repr__(self) -> str:
        return f"OpenAPIResourceTemplate(name={self.name!r}, uri_template={self.uri_template!r}, path={self._route.path})"

    async def create_resource(
        self,
        uri: str,
        params: dict[str, Any],
        context: Context | None = None,
    ) -> Resource:
        """Create a resource with the given parameters."""
        uri_parts = [f"{key}={value}" for key, value in params.items()]
        arguments = {}
        for parameter in self._route.parameters:
            if parameter.location != "path":
                continue
            argument_name = _path_argument_name(self._route, parameter.name)
            if parameter.name in params:
                arguments[argument_name] = params[parameter.name]
                continue
            normalized_name = parameter.name.replace("-", "_")
            if normalized_name in params:
                arguments[argument_name] = params[normalized_name]

        return OpenAPIResource(
            client=self._client,
            route=self._route,
            director=self._director,
            uri=uri,
            name=f"{self.name}-{'-'.join(uri_parts)}",
            description=self.description or f"Resource for {self._route.path}",
            mime_type=self.mime_type,
            tags=set(self._route.tags or []),
            arguments=arguments,
        )
