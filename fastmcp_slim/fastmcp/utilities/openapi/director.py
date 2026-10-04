"""Request director using openapi-core for stateless HTTP request building."""

import io
import json as _json
from typing import Any, ClassVar
from urllib.parse import quote, urljoin

import httpx
from jsonschema_path import SchemaPath

from fastmcp.utilities.logging import get_logger

from .models import HTTPRoute, ParameterInfo
from .schemas import _combine_schemas_and_map_params

logger = get_logger(__name__)


def _query_scalar_to_str(value: Any) -> str:
    """Convert a scalar to its query-string representation.

    Booleans are lowercased to match JSON/OpenAPI conventions (true/false)
    rather than Python's str(True) → "True".
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _allof_members(
    schema: dict[str, Any],
    schema_defs: dict[str, Any],
    resolving: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Expand local schema references while collecting `allOf` members."""
    resolving = resolving or set()

    ref = schema.get("$ref")
    if isinstance(ref, str):
        for prefix in ("#/$defs/", "#/components/schemas/"):
            if ref.startswith(prefix):
                name = ref.removeprefix(prefix)
                referenced_schema = schema_defs.get(name)
                if isinstance(referenced_schema, dict) and name not in resolving:
                    siblings = {
                        key: value for key, value in schema.items() if key != "$ref"
                    }
                    members = _allof_members(
                        referenced_schema, schema_defs, resolving | {name}
                    )
                    return members + ([siblings] if siblings else [])
                break

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        members = []
        for member in all_of:
            if isinstance(member, dict):
                members.extend(_allof_members(member, schema_defs, resolving))

        siblings = {key: value for key, value in schema.items() if key != "allOf"}
        return members + ([siblings] if siblings else [])

    return [schema]


def _prepare_parameter_map(
    route: HTTPRoute,
) -> tuple[HTTPRoute, dict[str, dict[str, str]]]:
    """Return the route's parameter map, building it if the route lacks one.

    Routes from the OpenAPI parser carry a precomputed map. Routes constructed
    directly may declare parameters or a request body without one; those get
    a map from the parser's map builder, computed on a copy so the caller's
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
    if request_body:
        # The map builder merges only inline `allOf` members, so expand
        # referenced members first to keep their properties.
        for body_schema in request_body.content_schema.values():
            all_of = body_schema.get("allOf")
            if isinstance(all_of, list):
                body_schema["allOf"] = [
                    expanded
                    for member in all_of
                    if isinstance(member, dict)
                    for expanded in _allof_members(member, route.request_schemas)
                ]
                own = {
                    key: body_schema[key]
                    for key in ("properties", "required")
                    if key in body_schema
                }
                if own:
                    body_schema["allOf"].append(own)
    prepared = route.model_copy(update={"request_body": request_body})
    _, parameter_map = _combine_schemas_and_map_params(prepared, convert_refs=False)
    for param in route.parameters:
        parameter_map.setdefault(
            f"{param.name}__{param.location}",
            {"location": param.location, "openapi_name": param.name},
        )
    return prepared, parameter_map


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
        json_body: dict[str, Any] | list[Any] | None = None
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
        files: dict[str, Any] | None = None
        data: dict[str, Any] | None = None
        if body is not None:
            if declared_content_type == "multipart/form-data" and isinstance(
                body, dict
            ):
                # Wrap plain values as (None, stringified) tuples so httpx
                # treats them as form fields. Scalars must be stringified
                # because httpx rejects non-string/bytes values in files=.
                # Use _query_scalar_to_str for booleans (true/false, not True/False).
                files = {}
                for k, v in body.items():
                    if isinstance(v, tuple):
                        files[k] = v
                    elif isinstance(v, bytes | io.IOBase):
                        # bytes and file-like objects are passed directly so
                        # httpx can transmit them as binary parts without
                        # stringifying to their Python repr.
                        files[k] = (None, v)
                    else:
                        files[k] = (None, _query_scalar_to_str(v))
            elif (
                declared_content_type == "application/x-www-form-urlencoded"
                and isinstance(body, dict)
            ):
                data = body
            elif isinstance(body, dict | list):
                if (
                    declared_content_type is not None
                    and declared_content_type != "application/json"
                    and "json" in declared_content_type
                ):
                    # JSON-compatible types like application/json-patch+json
                    # or application/merge-patch+json need an explicit
                    # Content-Type header since httpx's json= always
                    # sets application/json.
                    content = _json.dumps(body, allow_nan=False).encode("utf-8")
                    headers = dict(headers) if headers else {}
                    headers["Content-Type"] = raw_content_type
                else:
                    json_body = body
            else:
                content = body

        # Step 7: Create httpx.Request
        return httpx.Request(
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

        built_map = not route.parameter_map
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

                # A map built here for a free-form object body holds the whole
                # body in a single argument.
                is_whole_body = (
                    built_map
                    and len(body_props) == 1
                    and not body_schema.get("properties")
                )
                if (
                    isinstance(body_schema, dict)
                    and body_schema.get("type") == "object"
                    and not is_whole_body
                ):
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
        so httpx repeats the key (e.g. values=a&values=b). When explode=false,
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
                explode = param_info.explode if param_info.explode is not None else True
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
                        style = param_info.style or "form"
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
                    style = param_info.style or "form"
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
        """
        # Substitute path parameters with URL-encoding to prevent
        # path traversal and SSRF via crafted parameter values
        url_path = path_template
        for param_name, param_value in path_params.items():
            placeholder = f"{{{param_name}}}"
            if placeholder in url_path:
                safe_value = quote(str(param_value), safe="").replace(".", "%2E")
                url_path = url_path.replace(placeholder, safe_value)

        # Combine with base URL
        return urljoin(base_url.rstrip("/") + "/", url_path.lstrip("/"))


# Export public symbols
__all__ = ["RequestDirector"]
