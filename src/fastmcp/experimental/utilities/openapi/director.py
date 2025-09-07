"""Request director using openapi-core for stateless HTTP request building."""

from typing import Any
from urllib.parse import urljoin

import httpx
from jsonschema_path import SchemaPath

from fastmcp.utilities.logging import get_logger

from .models import HTTPRoute

logger = get_logger(__name__)


class RequestDirector:
    """Builds httpx.Request objects from HTTPRoute and arguments using openapi-core."""

    def __init__(self, spec: SchemaPath):
        """Initialize with a parsed SchemaPath object."""
        self._spec = spec

    def build(
        self,
        route: HTTPRoute,
        flat_args: dict[str, Any],
        base_url: str = "http://localhost",
    ) -> httpx.Request:
        """
        Constructs a final httpx.Request object, handling all OpenAPI serialization.

        Args:
            route: HTTPRoute containing OpenAPI operation details
            flat_args: Flattened arguments from LLM (may include suffixed parameters)
            base_url: Base URL for the request

        Returns:
            httpx.Request: Properly formatted HTTP request
        """
        logger.debug(
            f"Building request for {route.method} {route.path} with args: {flat_args}"
        )

        # Step 1: Un-flatten arguments into path, query, body, etc. using parameter map
        path_params, query_params, header_params, body = self._unflatten_arguments(
            route, flat_args
        )

        logger.debug(
            f"Unflattened - path: {path_params}, query: {query_params}, headers: {header_params}, body: {body}"
        )

        # Step 2: Build base URL with path parameters
        url = self._build_url(route.path, path_params, base_url)

        # Step 3: Prepare request data
        request_data = {
            "method": route.method.upper(),
            "url": url,
            "params": query_params if query_params else None,
            "headers": header_params if header_params else None,
        }

        # Step 4: Handle request body
        if body is not None:
            if isinstance(body, dict) or isinstance(body, list):
                request_data["json"] = body
            else:
                request_data["content"] = body

        # Step 5: Create httpx.Request
        return httpx.Request(
            method=request_data["method"],
            url=request_data["url"],
            params=request_data.get("params"),
            headers=request_data.get("headers"),
            json=request_data.get("json"),
            content=request_data.get("content"),
        )

    def _unflatten_arguments(
        self, route: HTTPRoute, flat_args: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]:
        """
        Maps flat arguments back to their OpenAPI locations using the parameter map.

        Args:
            route: HTTPRoute with parameter_map containing location mappings
            flat_args: Flat arguments from LLM call

        Returns:
            Tuple of (path_params, query_params, header_params, body)
        """
        path_params = {}
        query_params = {}
        header_params = {}
        body_props = {}

        # Use parameter map to route arguments to correct locations
        if hasattr(route, "parameter_map") and route.parameter_map:
            for arg_name, value in flat_args.items():
                if value is None:
                    continue  # Skip None values for optional parameters

                if arg_name not in route.parameter_map:
                    logger.warning(
                        f"Argument '{arg_name}' not found in parameter map for {route.operation_id}"
                    )
                    continue

                mapping = route.parameter_map[arg_name]
                location = mapping["location"]
                openapi_name = mapping["openapi_name"]

                if location == "path":
                    path_params[openapi_name] = value
                elif location == "query":
                    query_params[openapi_name] = value
                elif location == "header":
                    header_params[openapi_name] = value
                elif location == "body":
                    body_props[openapi_name] = value
                else:
                    logger.warning(
                        f"Unknown parameter location '{location}' for {arg_name}"
                    )
        else:
            # Fallback: try to map arguments based on parameter definitions
            logger.debug("No parameter map available, using fallback mapping")

            # Create a mapping from parameter names to their locations
            param_locations = {}
            for param in route.parameters:
                param_locations[param.name] = param.location

            # Map arguments to locations
            for arg_name, value in flat_args.items():
                if value is None:
                    continue

                # Check if it's a suffixed parameter (e.g., id__path)
                if "__" in arg_name:
                    base_name, location = arg_name.rsplit("__", 1)
                    if location in ["path", "query", "header"]:
                        if location == "path":
                            path_params[base_name] = value
                        elif location == "query":
                            query_params[base_name] = value
                        elif location == "header":
                            header_params[base_name] = value
                        continue

                # Check if it's a known parameter
                if arg_name in param_locations:
                    location = param_locations[arg_name]
                    if location == "path":
                        path_params[arg_name] = value
                    elif location == "query":
                        query_params[arg_name] = value
                    elif location == "header":
                        header_params[arg_name] = value
                else:
                    # Assume it's a body property
                    body_props[arg_name] = value

        # Handle body construction
        body = None
        if body_props:
            # If we have body properties, construct the body object
            if route.request_body and route.request_body.content_schema:
                # Check if the request body expects an object with properties
                content_type = next(iter(route.request_body.content_schema))
                body_schema = route.request_body.content_schema[content_type]

                if body_schema.get("type") == "object":
                    body = body_props
                elif len(body_props) == 1:
                    # If body schema is not an object and we have exactly one property,
                    # use the property value directly
                    body = next(iter(body_props.values()))
                else:
                    # Multiple properties but schema is not object - wrap in object
                    body = body_props
            else:
                body = body_props

        return path_params, query_params, header_params, body

    def _build_url(
        self, path_template: str, path_params: dict[str, Any], base_url: str
    ) -> str:
        """
        Build URL by substituting path parameters in the template.

        Args:
            path_template: OpenAPI path template (e.g., "/users/{id}")
            path_params: Path parameter values
            base_url: Base URL to prepend

        Returns:
            Complete URL with path parameters substituted
        """
        # Substitute path parameters
        url_path = path_template
        for param_name, param_value in path_params.items():
            placeholder = f"{{{param_name}}}"
            if placeholder in url_path:
                url_path = url_path.replace(placeholder, str(param_value))

        # Combine with base URL
        return urljoin(base_url.rstrip("/") + "/", url_path.lstrip("/"))


# Export public symbols
__all__ = ["RequestDirector"]
