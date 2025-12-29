"""OpenAPI component classes: Tool, Resource, and ResourceTemplate."""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from mcp.types import ToolAnnotations
from pydantic.networks import AnyUrl

import fastmcp
from fastmcp.resources import (
    Resource,
    ResourceContent,
    ResourceResult,
    ResourceTemplate,
)
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.openapi import HTTPRoute
from fastmcp.utilities.openapi.director import RequestDirector

if TYPE_CHECKING:
    from fastmcp.server import Context

__all__ = [
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
]

logger = get_logger(__name__)


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
        client: httpx.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        name: str,
        description: str,
        parameters: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        tags: set[str] | None = None,
        timeout: float | None = None,
        annotations: ToolAnnotations | None = None,
        serializer: Callable[[Any], str] | None = None,  # Deprecated
    ):
        if serializer is not None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "The `serializer` parameter is deprecated. "
                "Return ToolResult from your tools for full control over serialization. "
                "See https://gofastmcp.com/servers/tools#custom-serialization for migration examples.",
                DeprecationWarning,
                stacklevel=2,
            )
        super().__init__(
            name=name,
            description=description,
            parameters=parameters,
            output_schema=output_schema,
            tags=tags or set(),
            annotations=annotations,
            serializer=serializer,
        )
        self._client = client
        self._route = route
        self._director = director
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"OpenAPITool(name={self.name!r}, method={self._route.method}, path={self._route.path})"

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the HTTP request using RequestDirector."""
        try:
            base_url = str(self._client.base_url) or "http://localhost"

            # Build the request using RequestDirector
            request = self._director.build(self._route, arguments, base_url)

            # Add client headers (lowest precedence)
            if self._client.headers:
                for key, value in self._client.headers.items():
                    if key not in request.headers:
                        request.headers[key] = value

            # Add MCP transport headers (highest precedence)
            mcp_headers = get_http_headers()
            if mcp_headers:
                request.headers.update(mcp_headers)

            logger.debug(f"run - sending request; headers: {request.headers}")

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

                return ToolResult(structured_content=structured_output)
            except json.JSONDecodeError:
                return ToolResult(content=response.text)

        except httpx.HTTPStatusError as e:
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"
            raise ValueError(error_message) from e

        except httpx.RequestError as e:
            raise ValueError(f"Request error: {e!s}") from e


class OpenAPIResource(Resource):
    """Resource implementation for OpenAPI endpoints."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "application/json",
        tags: set[str] | None = None,
        timeout: float | None = None,
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
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"OpenAPIResource(name={self.name!r}, uri={self.uri!r}, path={self._route.path})"

    async def read(self) -> ResourceResult:
        """Fetch the resource data by making an HTTP request."""
        try:
            path = self._route.path
            resource_uri = str(self.uri)

            # If this is a templated resource, extract path parameters from the URI
            if "{" in path and "}" in path:
                parts = resource_uri.split("/")

                if len(parts) > 1:
                    path_params = {}
                    param_matches = re.findall(r"\{([^}]+)\}", path)
                    if param_matches:
                        param_matches.sort(reverse=True)
                        expected_param_count = len(parts) - 1
                        for i, param_name in enumerate(param_matches):
                            if i < expected_param_count:
                                param_value = parts[-1 - i]
                                path_params[param_name] = param_value

                    for param_name, param_value in path_params.items():
                        path = path.replace(f"{{{param_name}}}", str(param_value))

            # Build headers with correct precedence
            headers: dict[str, str] = {}
            if self._client.headers:
                headers.update(self._client.headers)
            mcp_headers = get_http_headers()
            if mcp_headers:
                headers.update(mcp_headers)

            response = await self._client.request(
                method=self._route.method,
                url=path,
                headers=headers,
                timeout=self._timeout,
            )
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

        except httpx.HTTPStatusError as e:
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"
            raise ValueError(error_message) from e

        except httpx.RequestError as e:
            raise ValueError(f"Request error: {e!s}") from e


class OpenAPIResourceTemplate(ResourceTemplate):
    """Resource template implementation for OpenAPI endpoints."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        uri_template: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        tags: set[str] | None = None,
        timeout: float | None = None,
    ):
        super().__init__(
            uri_template=uri_template,
            name=name,
            description=description,
            parameters=parameters,
            tags=tags or set(),
        )
        self._client = client
        self._route = route
        self._director = director
        self._timeout = timeout

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

        return OpenAPIResource(
            client=self._client,
            route=self._route,
            director=self._director,
            uri=uri,
            name=f"{self.name}-{'-'.join(uri_parts)}",
            description=self.description or f"Resource for {self._route.path}",
            mime_type="application/json",
            tags=set(self._route.tags or []),
            timeout=self._timeout,
        )
