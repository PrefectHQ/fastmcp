from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from mcp.server.auth.middleware.auth_context import (
    get_access_token as _sdk_get_access_token,
)
from mcp.server.auth.provider import (
    AccessToken as _SDKAccessToken,
)
from starlette.requests import Request

from fastmcp.server.auth import AccessToken

if TYPE_CHECKING:
    from fastmcp.server.context import Context

__all__ = [
    "AccessToken",
    "get_access_token",
    "get_context",
    "get_http_headers",
    "get_http_request",
]


# --- Context ---


def get_context() -> Context:
    from fastmcp.server.context import _current_context

    context = _current_context.get()
    if context is None:
        raise RuntimeError("No active context found.")
    return context


# --- HTTP Request ---


def get_http_request() -> Request:
    from mcp.server.lowlevel.server import request_ctx

    request = None
    with contextlib.suppress(LookupError):
        request = request_ctx.get().request

    if request is None:
        raise RuntimeError("No active HTTP request found.")
    return request


def get_http_headers(include_all: bool = False) -> dict[str, str]:
    """
    Extract headers from the current HTTP request if available.

    Never raises an exception, even if there is no active HTTP request (in which case
    an empty dict is returned).

    By default, strips problematic headers like `content-length` that cause issues if forwarded to downstream clients.
    If `include_all` is True, all headers are returned.
    """
    if include_all:
        exclude_headers = set()
    else:
        exclude_headers = {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "upgrade",
            "te",
            "keep-alive",
            "expect",
            "accept",
            # Proxy-related headers
            "proxy-authenticate",
            "proxy-authorization",
            "proxy-connection",
            # MCP-related headers
            "mcp-session-id",
        }
        # (just in case)
        if not all(h.lower() == h for h in exclude_headers):
            raise ValueError("Excluded headers must be lowercase")
    headers = {}

    try:
        request = get_http_request()
        for name, value in request.headers.items():
            lower_name = name.lower()
            if lower_name not in exclude_headers:
                headers[lower_name] = str(value)
        return headers
    except RuntimeError:
        return {}


# --- Access Token ---


def get_access_token() -> AccessToken | None:
    """
    Get the FastMCP access token from the current context.

    Returns:
        The access token if an authenticated user is available, None otherwise.
    """
    #
    access_token: _SDKAccessToken | None = _sdk_get_access_token()

    if access_token is None or isinstance(access_token, AccessToken):
        return access_token

    # If the object is not a FastMCP AccessToken, convert it to one if the fields are compatible
    # This is a workaround for the case where the SDK returns a different type
    # If it fails, it will raise a TypeError
    try:
        access_token_as_dict = access_token.model_dump()
        return AccessToken(
            token=access_token_as_dict["token"],
            client_id=access_token_as_dict["client_id"],
            scopes=access_token_as_dict["scopes"],
            # Optional fields
            expires_at=access_token_as_dict.get("expires_at"),
            resource_owner=access_token_as_dict.get("resource_owner"),
            claims=access_token_as_dict.get("claims"),
        )
    except Exception as e:
        raise TypeError(
            f"Expected fastmcp.server.auth.auth.AccessToken, got {type(access_token).__name__}. "
            "Ensure the SDK is using the correct AccessToken type."
        ) from e
