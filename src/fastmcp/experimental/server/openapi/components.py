"""OpenAPI component implementations: Tool, Resource, and ResourceTemplate classes."""

import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from mcp.types import ToolAnnotations
from pydantic.networks import AnyUrl

# Import from our new utilities
from fastmcp.experimental.utilities.openapi import HTTPRoute
from fastmcp.experimental.utilities.openapi.director import RequestDirector
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server import Context

logger = get_logger(__name__)


class OpenAPITool(Tool):
    """Tool implementation for OpenAPI endpoints."""

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
        serializer: Callable[[Any], str] | None = None,
    ):
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
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPITool(name={self.name!r}, method={self._route.method}, path={self._route.path})"

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the HTTP request using RequestDirector for simplified parameter handling."""
        try:
            # Get base URL from client
            base_url = (
                str(self._client.base_url)
                if hasattr(self._client, "base_url") and self._client.base_url
                else "http://localhost"
            )

            # Get Headers from client
            cli_headers = (
                self._client.headers
                if hasattr(self._client, "headers") and self._client.headers
                else {}
            )

            # Build the request using RequestDirector
            request = self._director.build(self._route, arguments, base_url)

            # First add server headers (lowest precedence)
            if cli_headers:
                # Merge with existing headers, _client headers as base
                if request.headers:
                    # Start with request headers, then add client headers
                    for key, value in cli_headers.items():
                        if key not in request.headers:
                            request.headers[key] = value
                else:
                    # Create new headers from cli_headers
                    for key, value in cli_headers.items():
                        request.headers[key] = value

            # Then add MCP client transport headers (highest precedence)
            mcp_headers = get_http_headers()
            if mcp_headers:
                # Merge with existing headers, MCP headers take precedence over all
                if request.headers:
                    request.headers.update(mcp_headers)
                else:
                    # Create new headers from mcp_headers
                    for key, value in mcp_headers.items():
                        request.headers[key] = value
            # print logger
            logger.debug(f"run - sending request; headers: {request.headers}")

            # Execute the request
            # Note: httpx.AsyncClient.send() doesn't accept timeout parameter
            # The timeout should be configured on the client itself
            response = await self._client.send(request)

            # Raise for 4xx/5xx responses
            response.raise_for_status()

            # Try to parse as JSON first
            try:
                result = response.json()

                # Handle structured content based on output schema, if any
                structured_output = None
                if self.output_schema is not None:
                    if self.output_schema.get("x-fastmcp-wrap-result"):
                        # Schema says wrap - always wrap in result key
                        structured_output = {"result": result}
                    else:
                        structured_output = result
                # If no output schema, use fallback logic for backward compatibility
                elif not isinstance(result, dict):
                    structured_output = {"result": result}
                else:
                    structured_output = result

                return ToolResult(structured_content=structured_output)
            except json.JSONDecodeError:
                return ToolResult(content=response.text)

        except httpx.HTTPStatusError as e:
            # Handle HTTP errors (4xx, 5xx)
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"

            raise ValueError(error_message)

        except httpx.RequestError as e:
            # Handle request errors (connection, timeout, etc.)
            raise ValueError(f"Request error: {str(e)}")


class OpenAPIResource(Resource):
    """Resource implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "application/json",
        tags: set[str] = set(),
        timeout: float | None = None,
    ):
        super().__init__(
            uri=AnyUrl(uri),  # Convert string to AnyUrl
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
        )
        self._client = client
        self._route = route
        self._director = director
        self._timeout = timeout

    def __repr__(self) -> str:
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPIResource(name={self.name!r}, uri={self.uri!r}, path={self._route.path})"

    async def read(self) -> str | bytes:
        """Fetch the resource data by making an HTTP request."""
        try:
            # Extract path parameters from the URI if present
            path = self._route.path
            resource_uri = str(self.uri)

            # If this is a templated resource, extract path parameters from the URI
            if "{" in path and "}" in path:
                # Extract the resource ID from the URI (the last part after the last slash)
                parts = resource_uri.split("/")

                if len(parts) > 1:
                    # Find all path parameters in the route path
                    path_params = {}

                    # Find the path parameter names from the route path
                    param_matches = re.findall(r"\{([^}]+)\}", path)
                    if param_matches:
                        # Reverse sorting from creation order (traversal is backwards)
                        param_matches.sort(reverse=True)
                        # Number of sent parameters is number of parts -1 (assuming first part is resource identifier)
                        expected_param_count = len(parts) - 1
                        # Map parameters from the end of the URI to the parameters in the path
                        # Last parameter in URI (parts[-1]) maps to last parameter in path, and so on
                        for i, param_name in enumerate(param_matches):
                            # Ensure we don't use resource identifier as parameter
                            if i < expected_param_count:
                                # Get values from the end of parts
                                param_value = parts[-1 - i]
                                path_params[param_name] = param_value

                    # Replace path parameters with their values
                    for param_name, param_value in path_params.items():
                        path = path.replace(f"{{{param_name}}}", str(param_value))

            # Filter any query parameters - get query parameters and filter out None/empty values
            query_params = {}
            for param in self._route.parameters:
                if param.location == "query" and hasattr(self, f"_{param.name}"):
                    value = getattr(self, f"_{param.name}")
                    if value is not None and value != "":
                        query_params[param.name] = value

            # Prepare headers with correct precedence: server < client transport
            headers = {}
            # Start with server headers (lowest precedence)
            cli_headers = (
                self._client.headers
                if hasattr(self._client, "headers") and self._client.headers
                else {}
            )
            headers.update(cli_headers)

            # Add MCP client transport headers (highest precedence)
            mcp_headers = get_http_headers()
            headers.update(mcp_headers)

            response = await self._client.request(
                method=self._route.method,
                url=path,
                params=query_params,
                headers=headers,
                timeout=self._timeout,
            )

            # Raise for 4xx/5xx responses
            response.raise_for_status()

            # Determine content type and return appropriate format
            content_type = response.headers.get("content-type", "").lower()

            if "application/json" in content_type:
                result = response.json()
                return json.dumps(result)
            elif any(ct in content_type for ct in ["text/", "application/xml"]):
                return response.text
            else:
                return response.content

        except httpx.HTTPStatusError as e:
            # Handle HTTP errors (4xx, 5xx)
            error_message = (
                f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
            )
            try:
                error_data = e.response.json()
                error_message += f" - {error_data}"
            except (json.JSONDecodeError, ValueError):
                if e.response.text:
                    error_message += f" - {e.response.text}"

            raise ValueError(error_message)

        except httpx.RequestError as e:
            # Handle request errors (connection, timeout, etc.)
            raise ValueError(f"Request error: {str(e)}")


class OpenAPIResourceTemplate(ResourceTemplate):
    """Resource template implementation for OpenAPI endpoints."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        route: HTTPRoute,
        director: RequestDirector,
        uri_template: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        tags: set[str] = set(),
        timeout: float | None = None,
    ):
        super().__init__(
            uri_template=uri_template,
            name=name,
            description=description,
            parameters=parameters,
            tags=tags,
        )
        self._client = client
        self._route = route
        self._director = director
        self._timeout = timeout

    def __repr__(self) -> str:
        """Custom representation to prevent recursion errors when printing."""
        return f"OpenAPIResourceTemplate(name={self.name!r}, uri_template={self.uri_template!r}, path={self._route.path})"

    async def create_resource(
        self,
        uri: str,
        params: dict[str, Any],
        context: "Context | None" = None,
    ) -> Resource:
        """Create a resource with the given parameters."""
        # Generate a URI for this resource instance
        uri_parts = []
        for key, value in params.items():
            uri_parts.append(f"{key}={value}")

        # Create and return a resource
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


# Export public symbols
__all__ = [
    "OpenAPITool",
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
]
