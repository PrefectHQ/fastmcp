"""Resource template functionality."""

from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Callable, Collection
from types import UnionType
from typing import Annotated, Any, ClassVar, Union, get_args, get_origin
from urllib.parse import parse_qs, quote, unquote, unquote_plus

from mcp_types import Annotations, Icon
from mcp_types import ResourceTemplate as SDKResourceTemplate
from pydantic import (
    Field,
    PrivateAttr,
    field_validator,
    validate_call,
)
from pydantic.json_schema import SkipJsonSchema

from fastmcp.resources.base import (
    Resource,
    ResourceResult,
    convert_raw_to_resource_result,
)
from fastmcp.resources.security import (
    INHERIT_SECURITY,
    InheritSecurity,
    ResourceSecurity,
)
from fastmcp.utilities.authorization import AuthCheck
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.mime import resolve_ui_mime_type
from fastmcp.utilities.types import get_cached_typeadapter


def extract_query_params(uri_template: str) -> set[str]:
    """Extract query parameter names from RFC 6570 `{?param1,param2}` syntax.

    The explode modifier is stripped, so `{?tags*}` yields `{"tags"}`. Use
    `extract_exploded_query_params` to find which names carried it.
    """
    match = re.search(r"\{\?([^}]+)\}", uri_template)
    if match:
        return {p.strip().removesuffix("*") for p in match.group(1).split(",")}
    return set()


def _is_list_only(annotation: Any) -> bool:
    """Whether an annotation accepts only lists, optionally wrapped or nullable."""
    if annotation is list:
        return True
    origin = get_origin(annotation)
    if origin is list:
        return True
    if origin is Annotated:
        return _is_list_only(get_args(annotation)[0])
    if origin in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        return bool(members) and all(_is_list_only(arg) for arg in members)
    return False


def _accepts_exploded_query(annotation: Any) -> bool:
    """Whether an annotation explicitly accepts the list produced by explode."""
    if (
        annotation is Any
        or annotation is object
        or annotation is inspect.Parameter.empty
        or isinstance(annotation, str)
    ):
        return True
    if annotation is list:
        return True
    origin = get_origin(annotation)
    if origin is list:
        return True
    if origin is Annotated:
        return _accepts_exploded_query(get_args(annotation)[0])
    if origin in (Union, UnionType):
        return any(_accepts_exploded_query(arg) for arg in get_args(annotation))
    return False


def extract_exploded_query_params(uri_template: str) -> set[str]:
    """Extract query parameter names declared with the RFC 6570 explode modifier.

    `{?tags*}` marks `tags` as repeatable — `?tags=a&tags=b` collects into a
    list rather than collapsing to the first value.
    """
    match = re.search(r"\{\?([^}]+)\}", uri_template)
    if match:
        names = [p.strip() for p in match.group(1).split(",")]
        return {n.removesuffix("*") for n in names if n.endswith("*")}
    return set()


# RFC 3986 reserved characters, which stay as written in a template literal.
_RESERVED = ":/?#[]@!$&'()*+,;="
_PCT_TRIPLET = re.compile(r"%[0-9A-Fa-f]{2}")


def encode_literal(text: str) -> str:
    """Percent-encode literal template text per RFC 6570 section 3.1.

    A template may be written with characters the URI grammar does not allow —
    a non-ASCII `ucschar` (`file:///docs/café/`) or a space — and section 3.1
    requires those to be UTF-8 percent-encoded in the expanded URI. Reserved
    and unreserved characters are structural and stay as written, and existing
    `%XX` triplets pass through so an already-encoded literal is not encoded
    twice.

    A resource URI reaches the server as an `AnyUrl`, which percent-encodes it,
    so the encoded form is what matching has to line up with.
    """
    out: list[str] = []
    last = 0
    for match in _PCT_TRIPLET.finditer(text):
        out.append(quote(text[last : match.start()], safe=_RESERVED))
        out.append(match.group())
        last = match.end()
    out.append(quote(text[last:], safe=_RESERVED))
    return "".join(out)


def _literal_pattern(text: str) -> str:
    """Regex for a literal run that matches it either as written or percent-encoded.

    `AnyUrl` percent-encodes some characters that section 3.1 requires encoding
    (non-ASCII, a space in a hierarchical path) but leaves others as written
    (`|`, `^`, `\\`, a stray `%`, a space in an opaque path), and in-process
    reads skip `AnyUrl` entirely. Each character that encoding would change
    therefore matches in both forms, with the hex digits in either case.
    """

    def chars(run: str) -> str:
        out = []
        for ch in run:
            encoded = quote(ch, safe=_RESERVED)
            if encoded == ch:
                out.append(re.escape(ch))
            else:
                out.append(f"(?:{re.escape(ch)}|(?i:{re.escape(encoded)}))")
        return "".join(out)

    pattern: list[str] = []
    last = 0
    for match in _PCT_TRIPLET.finditer(text):
        pattern.append(chars(text[last : match.start()]))
        pattern.append(f"(?i:{re.escape(match.group())})")
        last = match.end()
    pattern.append(chars(text[last:]))
    return "".join(pattern)


# Unbounded: keys are server-defined templates, and a read walks every template,
# so a capped cache would evict on every read once a server outgrew it.
@functools.cache
def build_regex(template: str) -> re.Pattern[str] | None:
    """Build regex pattern for URI template, handling RFC 6570 syntax.

    Supports:
    - `{var}` - simple path parameter
    - `{var*}` - wildcard path parameter (captures multiple segments)
    - `{?var1,var2}` - query parameters (ignored in path matching)

    Hyphens in parameter names are normalized to underscores in regex group
    names so that matched groups are valid Python identifiers.

    Returns None if the template produces an invalid regex (e.g. parameter
    names with leading digits or duplicates from a remote server).
    """
    # Remove query parameter syntax for path matching
    template_without_query = re.sub(r"\{\?[^}]+\}", "", template)

    parts = re.split(r"(\{[^}]+\})", template_without_query)
    pattern = ""
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            if name.endswith("*"):
                name = name[:-1]
                group = name.replace("-", "_")
                pattern += f"(?P<{group}>.+)"
            else:
                group = name.replace("-", "_")
                pattern += f"(?P<{group}>[^/]+)"
        else:
            pattern += _literal_pattern(part)
    try:
        return re.compile(f"^{pattern}$")
    except re.error:
        return None


def match_uri_template(
    uri: str, uri_template: str, *, list_params: Collection[str] = ()
) -> dict[str, Any] | None:
    """Match URI against template and extract both path and query parameters.

    Supports RFC 6570 URI templates:
    - Path params: `{var}`, `{var*}`
    - Query params: `{?var1,var2}`, `{?list*}` (repeated keys)

    `list_params` names non-exploded query params that hold lists. Per RFC 6570
    section 3.2.8 their value is comma-joined, with literal commas separating
    items and `%2C` inside an item, so they are split before decoding.
    """
    # Split URI into path and query parts
    uri_path, _, query_string = uri.partition("?")

    # Match path parameters
    regex = build_regex(uri_template)
    if regex is None:
        return None
    match = regex.match(uri_path)
    if not match:
        return None

    params: dict[str, Any] = {k: unquote(v) for k, v in match.groupdict().items()}

    # Extract query parameters if present in URI and template
    if query_string:
        query_param_names = extract_query_params(uri_template)
        # keep_blank_values=True preserves empty values (e.g. ?format=)
        # so callers can distinguish "explicitly empty" from "missing".
        parsed_query = parse_qs(query_string, keep_blank_values=True)

        exploded = extract_exploded_query_params(uri_template)
        raw_query: dict[str, str] = {}
        for pair in query_string.split("&"):
            raw_name, _, raw_value = pair.partition("=")
            raw_query.setdefault(unquote_plus(raw_name), raw_value)

        for name in query_param_names:
            if name in parsed_query:
                # Normalize hyphens to underscores to match Python param names.
                # Don't overwrite path params that were already extracted.
                key = name.replace("-", "_")
                if key not in params:
                    # An exploded `{?name*}` param keeps every repetition, a
                    # list `{?name}` param splits its first value on commas, and
                    # a plain `{?name}` param is a scalar, so take the first.
                    if name in exploded:
                        params[key] = parsed_query[name]
                    elif name in list_params:
                        raw = raw_query.get(name, "")
                        params[key] = (
                            [unquote_plus(item) for item in raw.split(",")]
                            if raw
                            else []
                        )
                    else:
                        params[key] = parsed_query[name][0]

    return params


def expand_uri_template(uri_template: str, params: dict[str, Any]) -> str:
    """Expand a URI template with parameters — inverse of `match_uri_template`.

    Supports the same RFC 6570 subset:
    - Path params: `{var}`, `{var*}`
    - Query params: `{?var1,var2}`
    """
    # Literal runs carry their own encoding (RFC 6570 3.1); placeholders are
    # left alone here and encoded with their substituted values below.
    result = "".join(
        part if part.startswith("{") and part.endswith("}") else encode_literal(part)
        for part in re.split(r"(\{[^}]+\})", uri_template)
    )

    # Replace {name} and {name*} path placeholders, percent-encoding the
    # substituted values so the result round-trips through match_uri_template
    # (which unquotes captured groups). Simple {name} placeholders match a
    # single segment ([^/]+), so reserved characters including "/" are encoded;
    # wildcard {name*} placeholders may span segments, so "/" is preserved.
    #
    # Params use underscored keys (e.g. user_id) but templates may use
    # hyphens (e.g. {user-id}), so try both forms.
    for key, value in params.items():
        value_str = str(value)
        simple = quote(value_str, safe="")
        wildcard = quote(value_str, safe="/")
        forms = [key]
        hyphenated = key.replace("_", "-")
        if hyphenated != key:
            forms.append(hyphenated)
        for form in forms:
            result = result.replace(f"{{{form}}}", simple)
            result = result.replace(f"{{{form}*}}", wildcard)

    # Expand {?param1,param2,...} query parameter blocks
    def _expand_query_block(match: re.Match[str]) -> str:
        names = [n.strip() for n in match.group(1).split(",")]
        parts = []
        for name in names:
            # The template decides the serialization, not the runtime value:
            # `{?tags*}` emits a repeated key, `{?tags}` stays a single value.
            # Keying off the value type instead would expand a list under a
            # plain `{?tags}`, which match_uri_template then reads back as just
            # its first element.
            exploded = name.endswith("*")
            name = name.removesuffix("*")
            underscored = name.replace("-", "_")
            if name in params:
                value = params[name]
            elif underscored in params:
                value = params[underscored]
            else:
                continue
            if exploded and isinstance(value, (list, tuple)):
                parts.extend(
                    f"{quote(name, safe='')}={quote(str(v), safe='')}" for v in value
                )
            elif isinstance(value, (list, tuple)):
                if value:
                    joined = ",".join(quote(str(v), safe="") for v in value)
                    parts.append(f"{quote(name, safe='')}={joined}")
            else:
                parts.append(f"{quote(name, safe='')}={quote(str(value), safe='')}")
        if parts:
            return "?" + "&".join(parts)
        return ""

    result = re.sub(r"\{\?([^}]+)\}", _expand_query_block, result)

    return result


def forward_uri(uri_template: str, params: dict[str, Any], uri: str) -> str:
    """Build the URI a forwarding layer (mount or proxy) sends to the server behind it.

    The path is expanded from `uri_template` with `params`, but the query string
    is carried over from the incoming `uri` byte for byte. Re-expanding it would
    decode and re-encode values, and the forwarding layer does not know which
    `{?name}` params hold comma-joined lists, so `?tags=a%2Cb,c` would arrive as
    one item instead of two.
    """
    path_template = re.sub(r"\{\?[^}]+\}", "", uri_template)
    forwarded = expand_uri_template(path_template, params)
    _, _, query = uri.partition("?")
    return f"{forwarded}?{query}" if query else forwarded


class ResourceTemplate(FastMCPComponent):
    """A template for dynamically creating resources."""

    KEY_PREFIX: ClassVar[str] = "template"

    # Non-exploded `{?name}` query params whose function parameter is a list.
    _list_query_params: frozenset[str] = PrivateAttr(default_factory=frozenset)

    uri_template: str = Field(
        description="URI template with parameters (e.g. weather://{city}/current)"
    )
    mime_type: str = Field(
        default="text/plain", description="MIME type of the resource content"
    )
    parameters: dict[str, Any] = Field(
        description="JSON schema for function parameters"
    )
    annotations: Annotations | None = Field(
        default=None, description="Optional annotations about the resource's behavior"
    )
    auth: SkipJsonSchema[AuthCheck | list[AuthCheck] | None] = Field(
        default=None,
        description="Authorization checks for this resource template",
        exclude=True,
    )
    security: SkipJsonSchema[ResourceSecurity | None | InheritSecurity] = Field(
        default=INHERIT_SECURITY,
        description=(
            "Path-safety policy for extracted parameters. INHERIT_SECURITY "
            "(default) inherits the server-wide default; None disables "
            "screening; a ResourceSecurity instance applies that explicit "
            "policy."
        ),
        exclude=True,
    )

    def resolve_security(
        self, server_default: ResourceSecurity | None
    ) -> ResourceSecurity | None:
        """Resolve the effective security policy for this template.

        A per-component ``security`` overrides the server default.
        ``INHERIT_SECURITY`` (the field default) inherits ``server_default``;
        an explicit ``None`` disables screening for this template.
        """
        if isinstance(self.security, InheritSecurity):
            return server_default
        return self.security

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri_template={self.uri_template!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        security: ResourceSecurity | None | InheritSecurity = INHERIT_SECURITY,
    ) -> FunctionResourceTemplate:
        return FunctionResourceTemplate.from_function(
            fn=fn,
            uri_template=uri_template,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            annotations=annotations,
            meta=meta,
            auth=auth,
            security=security,
        )

    @field_validator("mime_type", mode="before")
    @classmethod
    def set_default_mime_type(cls, mime_type: str | None) -> str:
        """Set default MIME type if not provided."""
        if mime_type:
            return mime_type
        return "text/plain"

    def matches(self, uri: str) -> dict[str, Any] | None:
        """Check if URI matches template and extract parameters."""
        return match_uri_template(
            uri, self.uri_template, list_params=self._list_query_params
        )

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content."""
        raise NotImplementedError(
            "Subclasses must implement read() or override create_resource()"
        )

    def convert_result(self, raw_value: Any) -> ResourceResult:
        """Convert a raw result to ResourceResult.

        This is used in two contexts:
        1. In _read() to convert user function return values to ResourceResult
        2. In tasks_result_handler() to convert Docket task results to ResourceResult

        Handles ResourceResult passthrough and converts raw values using
        ResourceResult's normalization. The template's own ``mime_type`` is
        forwarded so that reads match the MIME type the template advertises
        in ``resources/templates/list``.
        """
        return convert_raw_to_resource_result(
            raw_value, mime_type=self.mime_type, meta=self.meta
        )

    async def _read(self, uri: str, params: dict[str, Any]) -> ResourceResult:
        """Server entry point for template reads.

        The server calls this instead of create_resource()/read() directly so
        subclasses can customize dispatch (e.g. FastMCPProviderResourceTemplate
        delegates to child-server middleware).
        """
        resource = await self.create_resource(uri, params)
        result = await resource.read()
        return self.convert_result(result)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters.

        The base implementation does not support background tasks.
        Use FunctionResourceTemplate for task support.
        """
        raise NotImplementedError(
            "Subclasses must implement create_resource(). "
            "Use FunctionResourceTemplate for task support."
        )

    def to_mcp_template(
        self,
        **overrides: Any,
    ) -> SDKResourceTemplate:
        """Convert the resource template to an SDKResourceTemplate."""

        return SDKResourceTemplate(
            name=overrides.get("name", self.name),
            uri_template=overrides.get("uriTemplate", self.uri_template),
            description=overrides.get("description", self.description),
            mime_type=overrides.get("mimeType", self.mime_type),
            title=overrides.get("title", self.title),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta()
            ),
        )

    @classmethod
    def from_mcp_template(cls, mcp_template: SDKResourceTemplate) -> ResourceTemplate:
        """Creates a FastMCP ResourceTemplate from a raw MCP ResourceTemplate object."""
        # Note: This creates a simple ResourceTemplate instance. For function-based templates,
        # the original function is lost, which is expected for remote templates.
        return cls(
            uri_template=mcp_template.uri_template,
            name=mcp_template.name,
            description=mcp_template.description,
            mime_type=mcp_template.mime_type or "text/plain",
            parameters={},  # Remote templates don't have local parameters
        )

    @property
    def key(self) -> str:
        """The globally unique lookup key for this template."""
        base_key = self.make_key(self.uri_template)
        return f"{base_key}@{self.version or ''}"

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "resource_template",
            "fastmcp.provider.type": "LocalProvider",
        }


class FunctionResourceTemplate(ResourceTemplate):
    """A template for dynamically creating resources."""

    fn: SkipJsonSchema[Callable[..., Any]]

    async def _read(self, uri: str, params: dict[str, Any]) -> ResourceResult:
        """Optimized server entry point that skips ephemeral resource creation.

        For FunctionResourceTemplate, we can call read() directly instead of
        creating a temporary resource, which is more efficient.
        """
        # Call read() directly, skip resource creation
        result = await self.read(arguments=params)
        return self.convert_result(result)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters."""

        async def resource_read_fn() -> str | bytes | ResourceResult:
            # Call function and check if result is a coroutine
            result = await self.read(arguments=params)
            return result

        return Resource.from_function(
            fn=resource_read_fn,
            uri=uri,
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
            tags=self.tags,
            annotations=self.annotations,
            meta=self.meta,
            title=self.title,
            icons=self.icons,
            auth=self.auth,
        )

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content."""
        # Type coercion for query parameters (which arrive as strings)
        kwargs = arguments.copy()
        sig = inspect.signature(self.fn)
        for param_name, param_value in list(kwargs.items()):
            if param_name in sig.parameters and isinstance(param_value, str):
                param = sig.parameters[param_name]
                annotation = param.annotation

                if annotation is inspect.Parameter.empty or annotation is str:
                    continue

                try:
                    if annotation is int:
                        kwargs[param_name] = int(param_value)
                    elif annotation is float:
                        kwargs[param_name] = float(param_value)
                    elif annotation is bool:
                        lower = param_value.lower()
                        if lower in ("true", "1", "yes"):
                            kwargs[param_name] = True
                        elif lower in ("false", "0", "no"):
                            kwargs[param_name] = False
                        else:
                            raise ValueError(
                                f"Invalid boolean value for {param_name}: {param_value!r}"
                            )
                except (ValueError, AttributeError):
                    raise

        # self.fn is wrapped by without_injected_parameters which handles
        # dependency resolution internally, so we call it directly
        result = self.fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        return result

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        security: ResourceSecurity | None | InheritSecurity = INHERIT_SECURITY,
    ) -> FunctionResourceTemplate:
        """Create a template from a function."""

        func_name = name or getattr(fn, "__name__", None) or fn.__class__.__name__
        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Reject functions with *args
        # (**kwargs is allowed because the URI will define the parameter names)
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError(
                    "Functions with *args are not supported as resource templates"
                )

        # Extract path and query parameters from URI template.
        # Allow hyphens in names and normalize to underscores so they
        # match Python function parameter names.
        raw_path_params = set(re.findall(r"{([\w-]+)(?:\*)?}", uri_template))
        raw_query_params = extract_query_params(uri_template)

        # Detect collisions: two raw param names that normalize to the
        # same Python identifier (e.g. {user-id} and {user_id}).
        all_raw = raw_path_params | raw_query_params
        seen: dict[str, str] = {}
        for raw_name in sorted(all_raw):
            normalized = raw_name.replace("-", "_")
            if normalized in seen:
                raise ValueError(
                    f"URI template parameters '{seen[normalized]}' and "
                    f"'{raw_name}' both normalize to '{normalized}'. "
                    f"Use one or the other, not both."
                )
            seen[normalized] = raw_name

        path_params = {p.replace("-", "_") for p in raw_path_params}
        query_params = {p.replace("-", "_") for p in raw_query_params}
        all_uri_params = path_params | query_params

        if not all_uri_params:
            raise ValueError("URI template must contain at least one parameter")

        # Use wrapper to get user-facing parameters (excludes injected params)
        from fastmcp.server.dependencies import (
            transform_context_annotations,
            without_injected_parameters,
        )

        wrapper_fn = without_injected_parameters(fn)
        user_sig = inspect.signature(wrapper_fn)
        func_params = set(user_sig.parameters.keys())

        # Get required and optional function parameters
        required_params = {
            p
            for p in func_params
            if user_sig.parameters[p].default is inspect.Parameter.empty
            and user_sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
        }
        optional_params = {
            p
            for p in func_params
            if user_sig.parameters[p].default is not inspect.Parameter.empty
            and user_sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
        }

        # Validate RFC 6570 query parameters
        # Query params must be optional (have defaults)
        list_query_params: set[str] = set()
        if query_params:
            invalid_query_params = query_params - optional_params
            if invalid_query_params:
                raise ValueError(
                    f"Query parameters {invalid_query_params} must be optional function parameters with default values"
                )

            # A list-typed query parameter reads `{?tags*}` as repeated keys and
            # `{?tags}` as one comma-joined value (RFC 6570 section 3.2.8).
            #
            # Resolve the hints rather than reading the raw signature: under
            # `from __future__ import annotations` every annotation is a string,
            # and `Annotated[...]` wrappers hide the underlying type.
            from fastmcp.tools.function_tool import _resolve_param_hints

            try:
                hints = _resolve_param_hints(fn)
            except NameError:
                # Pydantic resolves annotations against namespaces this doesn't
                # see, so a name we can't resolve may still be valid. Fall back
                # to the raw form rather than failing a working registration.
                hints = {}

            exploded = {
                p.replace("-", "_") for p in extract_exploded_query_params(uri_template)
            }
            for param_name in sorted(exploded):
                if param_name not in user_sig.parameters:
                    continue
                annotation = hints.get(
                    param_name, user_sig.parameters[param_name].annotation
                )
                if not _accepts_exploded_query(annotation):
                    raise ValueError(
                        f"Query parameter '{param_name}' uses the RFC 6570 "
                        "explode modifier, so its function parameter must accept "
                        f"a list: use '{{?{param_name}}}' for a scalar value"
                    )
            for param_name in sorted(query_params - exploded):
                if param_name not in user_sig.parameters:
                    continue
                annotation = hints.get(
                    param_name, user_sig.parameters[param_name].annotation
                )
                if _is_list_only(annotation):
                    list_query_params.add(
                        next(
                            name
                            for name in extract_query_params(uri_template)
                            if name.replace("-", "_") == param_name
                        )
                    )

        # Check if required parameters are a subset of the path parameters
        if not required_params.issubset(path_params):
            raise ValueError(
                f"Required function arguments {required_params} must be a subset of the URI path parameters {path_params}"
            )

        # Check if all URI parameters are valid function parameters (skip if **kwargs present)
        if not any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        ):
            if not all_uri_params.issubset(func_params):
                raise ValueError(
                    f"URI parameters {all_uri_params} must be a subset of the function arguments: {func_params}"
                )

        description = description if description is not None else inspect.getdoc(fn)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn) and not isinstance(fn, functools.partial):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        wrapper_fn = without_injected_parameters(fn)
        type_adapter = get_cached_typeadapter(wrapper_fn)
        parameters = type_adapter.json_schema()
        parameters = compress_schema(parameters, prune_titles=True)

        # Use validate_call on wrapper for runtime type coercion
        fn = validate_call(wrapper_fn)

        # Apply ui:// MIME default, then fall back to text/plain
        resolved_mime = resolve_ui_mime_type(uri_template, mime_type)

        template = cls(
            uri_template=uri_template,
            name=func_name,
            version=str(version) if version is not None else None,
            title=title,
            description=description,
            icons=icons,
            mime_type=resolved_mime or "text/plain",
            fn=fn,
            parameters=parameters,
            tags=tags or set(),
            annotations=annotations,
            meta=meta,
            auth=auth,
            security=security,
        )
        template._list_query_params = frozenset(list_query_params)
        return template
