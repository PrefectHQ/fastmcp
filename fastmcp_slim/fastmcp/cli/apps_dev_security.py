"""Browser session, Host, and Origin checks for the `fastmcp dev apps` host."""

from __future__ import annotations

import ipaddress
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "fastmcp_dev_session"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})  # noqa: S104

# Paths that serve server-supplied content. Browsers isolate it in an opaque
# origin with only these capabilities, matching the launch page's iframe.
_SANDBOXED_PATHS = frozenset({"/ui-resource", "/mcp"})
_SANDBOX = "sandbox allow-scripts allow-forms"
# Pages the dev host itself embeds in an iframe.
_FRAMEABLE_PATHS = frozenset({"/picker-app", "/ui-resource"})

# Response headers the dev host controls; backend values are dropped.
_HOST_HEADERS = frozenset(
    {
        b"content-security-policy",
        b"referrer-policy",
        b"cache-control",
        b"x-content-type-options",
        b"set-cookie",
    }
)


class DevSessionMiddleware:
    """Require a browser session started from the private startup URL.

    `GET /?token=...` with the process token sets an HttpOnly, SameSite=Strict
    cookie and redirects to `/`. Every other request needs that cookie. Every
    request must also name this server in its Host header, and browser
    requests must come from this origin: cookies do not distinguish ports on
    the same host, so the Origin and Fetch Metadata headers are checked too.
    """

    def __init__(self, app: ASGIApp, *, host: str, port: int, token: str) -> None:
        self.app = app
        self.host = host.lower()
        self.port = port
        self.token = token
        self.cookie_name = f"{SESSION_COOKIE}_{port}"

    def _valid_host(self, headers: Headers) -> bool:
        values = headers.getlist("host")
        if len(values) != 1:
            return False
        value = values[0]
        try:
            authority = urlsplit(f"http://{value}")
            port = authority.port
        except ValueError:
            return False
        if authority.netloc != value or authority.username or authority.password:
            return False
        if port != self.port:
            return False
        hostname = authority.hostname or ""
        if self.host in _WILDCARD_HOSTS:
            # A server bound to every interface accepts literal addresses only;
            # a DNS name could point anywhere.
            if hostname == "localhost":
                return True
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                return False
            return True
        if self.host in _LOOPBACK_HOSTS:
            return hostname in _LOOPBACK_HOSTS
        return hostname == self.host

    def _same_origin(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is not None and origin != f"http://{request.headers['host']}":
            return False
        fetch_site = request.headers.get("sec-fetch-site")
        return fetch_site is None or fetch_site in {"same-origin", "none"}

    def _valid_token(self, value: str) -> bool:
        return secrets.compare_digest(value.encode(), self.token.encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # The dev host has no WebSocket endpoints.
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if not self._valid_host(request.headers):
            response = PlainTextResponse("Invalid Host header", status_code=400)
            await response(scope, receive, send)
            return
        if not self._same_origin(request):
            response = PlainTextResponse("Cross-origin request", status_code=403)
            await response(scope, receive, send)
            return

        path = request.url.path
        starting_session = False

        async def send_with_host_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in _HOST_HEADERS
                    or (starting_session and key.lower() == b"set-cookie")
                ]
                csp = "frame-ancestors " + (
                    "'self'" if path in _FRAMEABLE_PATHS else "'none'"
                )
                if path in _SANDBOXED_PATHS:
                    csp += f"; {_SANDBOX}"
                headers += [
                    (b"content-security-policy", csp.encode()),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
                message = {**message, "headers": headers}
            await send(message)

        if path == "/" and request.method == "GET":
            startup_token = request.query_params.get("token")
            if startup_token is not None and self._valid_token(startup_token):
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(
                    self.cookie_name,
                    self.token,
                    httponly=True,
                    samesite="strict",
                    path="/",
                )
                starting_session = True
                await response(scope, receive, send_with_host_headers)
                return

        if not self._valid_token(request.cookies.get(self.cookie_name, "")):
            response = PlainTextResponse(
                "No dev session. Open the dev UI with the startup URL printed "
                "by `fastmcp dev apps`.",
                status_code=403,
            )
            await response(scope, receive, send_with_host_headers)
            return

        await self.app(scope, receive, send_with_host_headers)
