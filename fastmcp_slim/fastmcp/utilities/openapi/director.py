"""Request director using openapi-core for stateless HTTP request building."""

import io
import json as _json
import re
from email.message import Message
from typing import Any, ClassVar
from urllib.parse import quote, unquote

import httpx2
from jsonschema_path import SchemaPath

from fastmcp.utilities.logging import get_logger

from .models import HTTPRoute, ParameterInfo
from .schemas import _combine_schemas_and_map_params, _ref_is_mergeable_object

logger = get_logger(__name__)

_MAX_PATH_DECODINGS = 32
_PATH_TEMPLATE_PARAM = re.compile(r"\{([^{}]+)\}")


def _query_scalar_to_str(value: Any) -> str:
    """Convert a scalar to its query-string representation.

    Booleans are lowercased to match JSON/OpenAPI conventions (true/false)
    rather than Python's str(True) → "True".
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _uses_default_multipart_encoding(encoding: dict[str, Any]) -> bool:
    """Explicit defaults serialize like omitted encoding; overrides stay unchanged."""
    defaults = {
        "style": "form",
        "explode": True,
        "contentType": "text/plain",
        "allowReserved": False,
        "headers": {},
    }
    return all(
        key in defaults and value == defaults[key] for key, value in encoding.items()
    )


def _prepare_parameter_map(
    route: HTTPRoute,
) -> tuple[HTTPRoute, dict[str, dict[str, str]]]:
    """Return the route's parameter map, building it if the route lacks one.

    Routes from the OpenAPI parser carry a precomputed map. Routes constructed
    directly may declare parameters or a request body without one; those get
    the map the parser would have built, computed on a copy so the caller's
    route is left unchanged. Those routes also accept `<name>__<location>`
    for each declared parameter, naming the parameter at that location. An
    empty map for a route with no parameters and no request body is already
    complete.
    """
    if route.parameter_map or not (route.parameters or route.request_body):
        return route, route.parameter_map

    request_body = (
        route.request_body.model_copy(deep=True) if route.request_body else None
    )
    prepared = route.model_copy(update={"request_body": request_body})
    _, parameter_map = _combine_schemas_and_map_params(prepared, convert_refs=False)
    for param in route.parameters:
        parameter_map.setdefault(
            f"{param.name}__{param.location}",
            {"location": param.location, "openapi_name": param.name},
        )
    return prepared, parameter_map


class RequestDirector:
    """Builds httpx2.Request objects from HTTPRoute and arguments using openapi-core."""

    def __init__(self, spec: SchemaPath):
        """Initialize with a parsed SchemaPath object."""
        self._spec = spec

    def build(
        self,
        route: HTTPRoute,
        flat_args: dict[str, Any],
        base_url: str = "http://localhost",
    ) -> httpx2.Request:
        """
        Constructs a final httpx2.Request object, handling all OpenAPI serialization.

        Args:
            route: HTTPRoute containing OpenAPI operation details
            flat_args: Flattened arguments from LLM (may include suffixed parameters)
            base_url: Base URL for the request

        Returns:
            httpx2.Request: Properly formatted HTTP request
        """
        logger.debug(
            f"Building request for {route.method} {route.path} with args: {flat_args}"
        )

        # Step 1: Un-flatten arguments into path, query, body, etc. using parameter map
        path_params, query_params, header_params, cookie_params, body = (
            self._unflatten_arguments(route, flat_args)
        )

        logger.debug(
            f"Unflattened - path: {path_params}, query: {query_params}, headers: {header_params}, body: {body}"
        )

        # Step 2: Serialize query parameters according to OpenAPI style/explode
        query_params = self._serialize_query_params(route, query_params)

        # Step 3: Build base URL with path parameters
        url = self._build_url(route.path, path_params, base_url)

        # Step 4: Prepare request data
        method: str = route.method.upper()
        params = query_params if query_params else None
        headers = header_params if header_params else None
        json_body: dict[str, Any] | list[Any] | str | int | float | bool | None = None
        content: str | bytes | None = None

        # Step 5: Determine the declared content type from the OpenAPI spec.
        # Use the raw value for outgoing Content-Type headers (preserves
        # parameters like charset), but normalize for dispatch matching.
        raw_content_type: str | None = None
        declared_content_type: str | None = None
        if route.request_body and route.request_body.content_schema:
            raw_content_type = next(iter(route.request_body.content_schema))
            declared_content_type = raw_content_type.split(";")[0].strip().lower()

        # httpx requires cookie values to be strings; use OpenAPI-style
        # serialization (e.g. true/false for booleans, not True/False)
        cookies = (
            {k: _query_scalar_to_str(v) for k, v in cookie_params.items()}
            if cookie_params
            else None
        )

        # Step 6: Handle request body — dispatch on declared content type.
        # httpx body kwargs (json, content, data, files) are mutually exclusive
        # but all accept None, so we set exactly one and pass all to Request().
        files: list[tuple[str, Any]] | None = None
        data: dict[str, Any] | None = None
        if body is not None:
            if declared_content_type == "multipart/form-data" and isinstance(
                body, dict
            ):
                # Wrap plain values as (None, stringified) tuples so httpx
                # treats them as form fields. Scalars must be stringified
                # because httpx rejects non-string/bytes values in files=.
                # Use _query_scalar_to_str for booleans (true/false, not True/False).
                files = []
                encoding = (
                    route.request_body.encoding.get(raw_content_type, {})
                    if route.request_body and raw_content_type is not None
                    else {}
                )
                for k, v in body.items():
                    if (
                        isinstance(v, list)
                        and v
                        and all(isinstance(item, str) for item in v)
                        and _uses_default_multipart_encoding(encoding.get(k, {}))
                    ):
                        files.extend((k, (None, item)) for item in v)
                    elif isinstance(v, tuple):
                        files.append((k, v))
                    elif isinstance(v, bytes | io.IOBase):
                        # bytes and file-like objects are passed directly so
                        # httpx can transmit them as binary parts without
                        # stringifying to their Python repr.
                        files.append((k, (None, v)))
                    else:
                        files.append((k, (None, _query_scalar_to_str(v))))
            elif (
                declared_content_type == "application/x-www-form-urlencoded"
                and isinstance(body, dict)
            ):
                data = body
            elif (
                isinstance(body, dict | list)
                or declared_content_type == "application/json"
                or (
                    declared_content_type is not None
                    and declared_content_type.endswith("+json")
                )
            ):
                if (
                    declared_content_type is not None
                    and raw_content_type != "application/json"
                    and "json" in declared_content_type
                ):
                    # JSON media types with parameters or custom subtypes need
                    # an explicit Content-Type header since httpx's json=
                    # always sets bare application/json.
                    content = _json.dumps(body, allow_nan=False).encode("utf-8")
                    headers = dict(headers) if headers else {}
                    headers["Content-Type"] = raw_content_type
                else:
                    json_body = body
            else:
                # httpx rejects numbers and booleans as raw content
                content = (
                    _query_scalar_to_str(body)
                    if isinstance(body, bool | int | float)
                    else body
                )
                if raw_content_type is not None:
                    headers = dict(headers) if headers else {}
                    headers["Content-Type"] = raw_content_type
                    if isinstance(content, str):
                        media_type = Message()
                        media_type["Content-Type"] = raw_content_type
                        content = content.encode(
                            media_type.get_content_charset() or "utf-8"
                        )

        # Step 7: Create httpx2.Request
        return httpx2.Request(
            method=method,
            url=url,
            params=params,
            headers=headers,
            json=json_body,
            content=content,
            data=data,
            files=files,
            cookies=cookies,
        )

    def _unflatten_arguments(
        self, route: HTTPRoute, flat_args: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], Any]:
        """
        Maps flat arguments back to their OpenAPI locations using the parameter map.

        The parameter map is the only source of argument locations. Arguments
        that are not in the map are dropped, so a route that declares no
        parameters and no request body sends none.

        Args:
            route: HTTPRoute with parameter_map containing location mappings
            flat_args: Flat arguments from LLM call

        Returns:
            Tuple of (path_params, query_params, header_params, cookie_params, body)
        """
        path_params = {}
        query_params = {}
        header_params = {}
        cookie_params = {}
        body_props = {}

        route, parameter_map = _prepare_parameter_map(route)

        for arg_name, value in flat_args.items():
            if value is None:
                continue  # Skip None values for optional parameters

            if arg_name not in parameter_map:
                logger.warning(
                    f"Argument '{arg_name}' not found in parameter map for {route.operation_id}"
                )
                continue

            mapping = parameter_map[arg_name]
            location = mapping["location"]
            openapi_name = mapping["openapi_name"]

            if location == "path":
                path_params[openapi_name] = value
            elif location == "query":
                query_params[openapi_name] = value
            elif location == "header":
                header_params[openapi_name] = value
            elif location == "cookie":
                cookie_params[openapi_name] = value
            elif location == "body":
                body_props[openapi_name] = value
            else:
                logger.warning(
                    f"Unknown parameter location '{location}' for {arg_name}"
                )

        # Handle body construction
        body = None
        if body_props:
            # If we have body properties, construct the body object
            if (
                route.request_body
                and route.request_body.content_schema
                and len(route.request_body.content_schema) > 0
            ):
                content_type = next(iter(route.request_body.content_schema))
                body_schema = route.request_body.content_schema[content_type]

                # Only named properties are flattened into individual arguments.
                if (
                    isinstance(body_schema, dict)
                    and body_schema.get("properties")
                    and _ref_is_mergeable_object(body_schema, route.request_schemas)
                ):
                    body = body_props
                elif len(body_props) == 1:
                    # Free-form objects, arrays, and primitives use a single
                    # argument containing the entire body.
                    body = next(iter(body_props.values()))
                else:
                    body = body_props
            else:
                body = body_props

        return path_params, query_params, header_params, cookie_params, body

    # Delimiter per OpenAPI style when explode=false
    _STYLE_DELIMITERS: ClassVar[dict[str, str]] = {
        "form": ",",
        "spaceDelimited": " ",
        "pipeDelimited": "|",
    }

    def _serialize_query_params(
        self,
        route: HTTPRoute,
        query_params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Serialize query parameter values according to their OpenAPI style/explode settings.

        By default (style=form, explode=true), list values are passed through as-is
        so httpx repeats the key (e.g. values=a&values=b). For pipeDelimited and
        spaceDelimited arrays, omitted explode defaults to false. Object values
        retain the existing default of true. When explode=false,
        list values are joined with the style-appropriate delimiter:
          - form (default): comma  (values=a,b)
          - pipeDelimited:  pipe   (values=a|b)
          - spaceDelimited: space  (values=a%20b)
        """
        if not query_params:
            return query_params

        # Build a lookup from openapi_name -> ParameterInfo for query params
        param_lookup: dict[str, ParameterInfo] = {
            p.name: p for p in route.parameters if p.location == "query"
        }

        serialized: dict[str, Any] = {}
        for key, value in query_params.items():
            param_info = param_lookup.get(key)
            if param_info is not None:
                style = param_info.style or "form"
                explode = param_info.explode if param_info.explode is not None else True
                if (
                    param_info.explode is None
                    and isinstance(value, list)
                    and style in {"pipeDelimited", "spaceDelimited"}
                ):
                    explode = False
                if isinstance(value, dict):
                    if not value:
                        continue
                    if explode:
                        for k, v in value.items():
                            # deepObject keeps the parent parameter name;
                            # form style emits each property as a bare key.
                            property_name = _query_scalar_to_str(k)
                            if param_info.style == "deepObject":
                                property_name = f"{key}[{property_name}]"
                            serialized[property_name] = _query_scalar_to_str(v)
                    else:
                        delimiter = self._STYLE_DELIMITERS.get(style, ",")
                        # form,explode=false on objects: key,value pairs
                        # e.g. {"R": 100, "G": 200} → "R,100,G,200"
                        parts: list[str] = []
                        for k, v in value.items():
                            parts.append(_query_scalar_to_str(k))
                            parts.append(_query_scalar_to_str(v))
                        serialized[key] = delimiter.join(parts)
                    continue
                if not explode:
                    delimiter = self._STYLE_DELIMITERS.get(style, ",")
                    if isinstance(value, list):
                        if not value:
                            continue
                        serialized[key] = delimiter.join(
                            _query_scalar_to_str(v) for v in value
                        )
                        continue
            serialized[key] = value
        return serialized

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

        Raises:
            ValueError: If the template has a parameter with no value
        """
        missing = [
            name
            for name in dict.fromkeys(_PATH_TEMPLATE_PARAM.findall(path_template))
            if name not in path_params
        ]
        if missing:
            names = ", ".join(repr(name) for name in missing)
            raise ValueError(f"Missing required path parameters: {{{names}}}")

        # Substitute path parameters with URL-encoding to prevent
        # path traversal and SSRF via crafted parameter values
        url_path = path_template
        for param_name, param_value in path_params.items():
            placeholder = f"{{{param_name}}}"
            if placeholder in url_path:
                decoded = str(param_value)
                for _ in range(_MAX_PATH_DECODINGS):
                    if any(
                        part in {".", ".."}
                        for part in decoded.replace("\\", "/").split("/")
                    ):
                        raise ValueError(
                            f"Path parameter '{param_name}' cannot contain dot segments"
                        )
                    expanded = unquote(decoded)
                    if expanded == decoded:
                        break
                    decoded = expanded
                else:
                    raise ValueError(
                        f"Path parameter '{param_name}' has too many encoding layers"
                    )
                safe_value = quote(str(param_value), safe="").replace(".", "%2E")
                url_path = url_path.replace(placeholder, safe_value)

        # Combine with base URL. Plain concatenation: urljoin would read a
        # leading "name:" segment (e.g. "/job-1:cancel") as a URL scheme.
        return f"{base_url.rstrip('/')}/{url_path.lstrip('/')}"


# Export public symbols
__all__ = ["RequestDirector"]
