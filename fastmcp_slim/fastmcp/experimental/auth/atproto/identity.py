"""AT Protocol identity resolution and authorization server discovery.

Every URL fetched here is derived from user input (a handle, a DID document, a
PDS's metadata), so all requests go through FastMCP's SSRF-safe fetch: HTTPS
only, public addresses only, DNS pinned, redirects disabled, bounded size.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlparse

from fastmcp.server.auth.ssrf import SSRFError, SSRFFetchError, ssrf_safe_fetch

MAX_DOCUMENT_BYTES = 64 * 1024

_HANDLE_RE = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)
_DISALLOWED_HANDLE_TLDS = frozenset(
    {"alt", "arpa", "example", "internal", "invalid", "local", "localhost", "onion"}
)
_DID_PLC_RE = re.compile(r"^did:plc:[a-z2-7]{24}$")
_DID_WEB_RE = re.compile(r"^did:web:[a-zA-Z0-9.-]+$")
_DID_RE = re.compile(r"^did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]$")

ATProtoErrorReason = Literal[
    "invalid_identifier",
    "handle_not_found",
    "identity_unavailable",
    "server_unavailable",
    "denied",
    "expired",
    "invalid_grant",
    "not_allowed",
]


class ATProtoError(Exception):
    """A classified AT Protocol sign-in failure.

    ``reason`` selects the message shown on the login page; the exception text
    is for logs only.
    """

    def __init__(self, reason: ATProtoErrorReason, detail: str) -> None:
        super().__init__(detail)
        self.reason: ATProtoErrorReason = reason


@dataclass(frozen=True)
class ResolvedIdentity:
    did: str
    handle: str | None
    pds_url: str


@dataclass(frozen=True)
class AuthorizationServer:
    issuer: str
    pushed_authorization_request_endpoint: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None


def is_did(value: str) -> bool:
    return _DID_RE.fullmatch(value) is not None


def is_handle(value: str) -> bool:
    if len(value) > 253 or _HANDLE_RE.fullmatch(value) is None:
        return False
    return value.rsplit(".", 1)[-1].lower() not in _DISALLOWED_HANDLE_TLDS


def normalize_identifier(raw: str) -> str:
    """Normalize login input to a handle or DID.

    Strips whitespace, an ``at://`` prefix, and a leading ``@``. Handles are
    case-insensitive and are lowercased; DIDs are returned byte-for-byte
    because ``did:web`` identifiers are case-sensitive.
    """
    value = raw.strip()
    if value.startswith("at://"):
        value = value[len("at://") :]
    value = value.lstrip("@").strip().rstrip("/")
    if value.startswith("did:"):
        return value
    return value.lower()


async def _fetch_json(url: str) -> dict[str, Any]:
    body = await ssrf_safe_fetch(url, max_size=MAX_DOCUMENT_BYTES)
    try:
        data = json.loads(body)
    except ValueError as e:
        raise ValueError(f"{url} did not return JSON") from e
    if not isinstance(data, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return data


def _https_origin(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return value


async def resolve_handle(handle: str, *, handle_resolver_url: str | None) -> str:
    """Resolve a handle to a DID.

    Tries the handle's own ``/.well-known/atproto-did`` first. Handles that are
    published only as DNS TXT records fall back to ``handle_resolver_url``'s
    ``com.atproto.identity.resolveHandle``. The resolved DID is only a routing
    hint: sign-in trusts the ``sub`` returned by the DID's own authorization
    server, never this lookup.
    """
    try:
        body = await ssrf_safe_fetch(
            f"https://{handle}/.well-known/atproto-did", max_size=512
        )
        did = body.decode("utf-8", errors="replace").strip()
        if is_did(did):
            return did
    except (SSRFError, SSRFFetchError):
        pass

    if handle_resolver_url:
        url = (
            f"{handle_resolver_url.rstrip('/')}"
            f"/xrpc/com.atproto.identity.resolveHandle?handle={quote(handle)}"
        )
        try:
            data = await _fetch_json(url)
        except (SSRFError, SSRFFetchError, ValueError):
            data = {}
        did = data.get("did")
        if isinstance(did, str) and is_did(did):
            return did

    raise ATProtoError("handle_not_found", f"Could not resolve handle {handle!r}")


async def resolve_did(did: str, *, plc_directory_url: str) -> ResolvedIdentity:
    """Resolve a ``did:plc`` or ``did:web`` to its handle and PDS."""
    if _DID_PLC_RE.fullmatch(did):
        url = f"{plc_directory_url.rstrip('/')}/{did}"
    elif _DID_WEB_RE.fullmatch(did):
        url = f"https://{did[len('did:web:') :]}/.well-known/did.json"
    else:
        raise ATProtoError("invalid_identifier", f"Unsupported DID method: {did!r}")

    try:
        document = await _fetch_json(url)
    except (SSRFError, SSRFFetchError, ValueError) as e:
        raise ATProtoError(
            "identity_unavailable", f"Could not resolve {did}: {e}"
        ) from e

    if document.get("id") != did:
        raise ATProtoError(
            "identity_unavailable", f"DID document for {did} has wrong id"
        )

    pds_url: str | None = None
    for service in document.get("service") or []:
        if not isinstance(service, dict):
            continue
        if service.get("id") in ("#atproto_pds", f"{did}#atproto_pds") and (
            service.get("type") == "AtprotoPersonalDataServer"
        ):
            pds_url = _https_url(service.get("serviceEndpoint"))
            break
    if pds_url is None:
        raise ATProtoError("identity_unavailable", f"{did} has no HTTPS PDS endpoint")

    handle: str | None = None
    for alias in document.get("alsoKnownAs") or []:
        if isinstance(alias, str) and alias.startswith("at://"):
            candidate = alias[len("at://") :].lower()
            if is_handle(candidate):
                handle = candidate
                break

    return ResolvedIdentity(did=did, handle=handle, pds_url=pds_url)


async def discover_authorization_server(pds_url: str) -> AuthorizationServer:
    """Find and validate the authorization server that protects a PDS."""
    try:
        resource = await _fetch_json(
            f"{pds_url.rstrip('/')}/.well-known/oauth-protected-resource"
        )
        servers = resource.get("authorization_servers")
        if not isinstance(servers, list) or not servers:
            raise ValueError("PDS lists no authorization servers")
        issuer = servers[0]
        if not isinstance(issuer, str) or not _https_origin(issuer):
            raise ValueError(f"Authorization server is not an HTTPS origin: {issuer!r}")
        issuer = issuer.rstrip("/")
        metadata = await _fetch_json(f"{issuer}/.well-known/oauth-authorization-server")
    except (SSRFError, SSRFFetchError, ValueError) as e:
        raise ATProtoError(
            "server_unavailable", f"Authorization server discovery failed: {e}"
        ) from e

    if metadata.get("issuer") != issuer:
        raise ATProtoError(
            "server_unavailable",
            f"Authorization server issuer mismatch: {metadata.get('issuer')!r} != {issuer!r}",
        )
    if "atproto" not in (metadata.get("scopes_supported") or []):
        raise ATProtoError(
            "server_unavailable", "Server does not support atproto scope"
        )
    if "ES256" not in (metadata.get("dpop_signing_alg_values_supported") or []):
        raise ATProtoError("server_unavailable", "Server does not support ES256 DPoP")

    endpoints = {
        name: _https_url(metadata.get(name))
        for name in (
            "pushed_authorization_request_endpoint",
            "authorization_endpoint",
            "token_endpoint",
        )
    }
    missing = [name for name, value in endpoints.items() if value is None]
    if missing:
        raise ATProtoError(
            "server_unavailable", f"Authorization server metadata lacks {missing}"
        )

    return AuthorizationServer(
        issuer=issuer,
        pushed_authorization_request_endpoint=endpoints[
            "pushed_authorization_request_endpoint"
        ]
        or "",
        authorization_endpoint=endpoints["authorization_endpoint"] or "",
        token_endpoint=endpoints["token_endpoint"] or "",
        revocation_endpoint=_https_url(metadata.get("revocation_endpoint")),
    )
