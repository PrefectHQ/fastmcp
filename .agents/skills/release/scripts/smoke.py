"""Release smoke for fastmcp, run against installed wheels (not the repo).

    python smoke.py            # prints one line per check, exits 1 on a FAIL

Covers a server and client over stdio and HTTP in both protocol eras, a proxy,
the OAuth proxy flow and the CLI, plus one PROBE per tightening / loosening /
new default since the previous release. PROBE lines report observed behavior
(run them against the previous release too); only CHECK lines can fail.

Each release keeps the CHECKs, drops the PROBEs for changes that already
shipped, and adds probes for its own tightenings and new defaults.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, patch

FASTMCP_VERSION = version("fastmcp")
WORK = Path(tempfile.mkdtemp(prefix="fastmcp-smoke-"))
results: list[tuple[str, str, str]] = []  # (kind, name, outcome)


def record(kind, name, fn):
    try:
        out = fn()
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        results.append((kind, name, f"ok: {out}" if kind == "CHECK" else str(out)))
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"[:200].replace("\n", " ")
        results.append((kind, name, ("FAIL " if kind == "CHECK" else "raised ") + msg))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


SERVER = textwrap.dedent("""
    import sys
    from fastmcp import FastMCP, Context
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS
    from mcp.types import InputRequiredResult, ElicitRequest, ElicitRequestFormParams
    mcp = FastMCP("smoke")
    @mcp.tool
    def add(a: int, b: int) -> int: return a + b
    @mcp.resource("data://x")
    def x() -> str: return "x"
    @mcp.prompt
    def p(n: str) -> str: return f"hi {n}"
    @mcp.tool
    async def chatty(ctx: Context) -> str:
        for i in range(3):
            await ctx.info(f"log {i}"); await ctx.report_progress(i + 1, 3)
        return "done"
    @mcp.tool
    async def ask(ctx: Context) -> str | InputRequiredResult:
        if ctx.request_context.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            r = await ctx.elicit("name?", response_type=str)
            return f"{r.action}:{getattr(r, 'data', None)}"
        if ctx.input_responses is None:
            return InputRequiredResult(result_type="input_required", input_requests={"n": ElicitRequest(
                method="elicitation/create", params=ElicitRequestFormParams(message="name?",
                requested_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}))})
        return f"{ctx.input_responses['n'].action}:{ctx.input_responses['n'].content['value']}"
    @mcp.tool
    async def grow() -> str:
        @mcp.tool(name="grown")
        def _g() -> str: return "g"
        notify = getattr(mcp, "notify_tools_changed", None)
        if notify: await notify()
        return "grew"
    if __name__ == "__main__":
        if len(sys.argv) > 1 and sys.argv[1] == "proxy":
            from fastmcp.server import create_proxy
            create_proxy(sys.argv[2]).run(transport="http", port=int(sys.argv[3]), show_banner=False, log_level="warning")
        elif len(sys.argv) > 1:
            mcp.run(transport="http", port=int(sys.argv[1]), show_banner=False, log_level="warning")
        else:
            mcp.run(show_banner=False)
""")
(WORK / "server.py").write_text(SERVER)


async def drive(target, mode):
    from fastmcp import Client
    from fastmcp.client.elicitation import ElicitResult

    logs, prog = [], []

    async def elicit(msg, rt, params, ctx):
        return ElicitResult(action="accept", content={"value": "smoke"})

    async def log(m):
        logs.append(m)

    async def progress(p, t, m):
        prog.append(p)

    async with Client(
        target,
        mode=mode,
        elicitation_handler=elicit,
        log_handler=log,
        progress_handler=progress,
    ) as c:
        assert (await c.call_tool("add", {"a": 1, "b": 2})).data == 3
        assert (await c.read_resource("data://x"))[0].text == "x"
        assert (await c.get_prompt("p", {"n": "a"})).messages
        assert (await c.call_tool("chatty", {})).data == "done"
        asked = (await c.call_tool("ask", {})).data
        assert asked == "accept:smoke", asked
    assert len(logs) == 3 and len(prog) == 3, (len(logs), len(prog))
    return "tools/resources/prompts/logs/progress/elicitation"


def start(*args, env=None):
    proc = subprocess.Popen(
        [sys.executable, "-I", str(WORK / "server.py"), *args],
        cwd=WORK,
        env={**os.environ, **(env or {})},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port = int(args[-1])
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"server on {port} never came up")


def with_http(fn, env=None):
    port = free_port()
    proc = start(str(port), env=env)
    try:
        return fn(f"http://127.0.0.1:{port}/mcp")
    finally:
        proc.terminate()
        proc.wait(5)


def with_proxy(fn):
    bport, pport = free_port(), free_port()
    b = start(str(bport))
    p = start("proxy", f"http://127.0.0.1:{bport}/mcp", str(pport))
    try:
        return fn(f"http://127.0.0.1:{pport}/mcp")
    finally:
        for proc in (p, b):
            proc.terminate()
            proc.wait(5)


# ---- CHECKS: core paths ----------------------------------------------------
from fastmcp.client.transports import StdioTransport  # noqa: E402

for mode in ("auto", "legacy"):
    record(
        "CHECK",
        f"stdio {mode}",
        lambda m=mode: drive(
            StdioTransport(
                sys.executable, ["-I", str(WORK / "server.py")], cwd=str(WORK)
            ),
            m,
        ),
    )
    record(
        "CHECK",
        f"http {mode}",
        lambda m=mode: with_http(lambda u: asyncio.run(drive(u, m))),
    )
    record(
        "CHECK",
        f"proxy {mode}",
        lambda m=mode: with_proxy(lambda u: asyncio.run(drive(u, m))),
    )


def cli():
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    fm = str(Path(sys.executable).parent / "fastmcp")
    out = subprocess.run(
        [fm, "call", "server.py", "add", "a=2", "b=5"],
        cwd=WORK,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert '"result": 7' in out.stdout, out.stdout + out.stderr
    out = subprocess.run(
        [fm, "list", "server.py"],
        cwd=WORK,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "add" in out.stdout, out.stdout + out.stderr
    out = subprocess.run(
        [fm, "generate-cli", "server.py", str(WORK / "gen.py")],
        cwd=WORK,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert (WORK / "gen.py").exists(), out.stdout + out.stderr
    out = subprocess.run(
        [
            sys.executable,
            str(WORK / "gen.py"),
            "call-tool",
            "add",
            "--a",
            "4",
            "--b",
            "4",
        ],
        cwd=WORK,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert '"result": 8' in out.stdout, out.stdout + out.stderr
    return "call, list, generate-cli + generated CLI"


record("CHECK", "cli", cli)


def make_proxy(**kw):
    from fastmcp.server.auth.oauth_proxy import OAuthProxy
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    return OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="up",
        upstream_client_secret="up-secret",
        token_verifier=JWTVerifier(public_key="x" * 32, algorithm="HS256"),
        base_url="https://proxy.test",
        jwt_signing_key="smoke-signing-key",
        **kw,
    )


async def oauth_flow():
    """Code exchange, refresh, concurrent replay, then revoke. Returns the facts the revoke probe reports."""
    import httpx2 as httpx
    from key_value.aio.stores.memory import MemoryStore
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from fastmcp import FastMCP
    from fastmcp.server.auth.oauth_proxy.models import ClientCode

    class SlowStore(MemoryStore):
        async def get(self, *a, **k):
            v = await super().get(*a, **k)
            await asyncio.sleep(0.05)
            return v

    proxy = make_proxy(client_storage=SlowStore())
    app = FastMCP("auth-smoke", auth=proxy).http_app()
    client = OAuthClientInformationFull(
        client_id="c1",
        client_secret="c1-secret",
        redirect_uris=[AnyUrl("http://localhost:1/cb")],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
    )
    await proxy.register_client(client)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    now = time.time()
    await proxy._code_store.put(
        key="code1",
        value=ClientCode(
            code="code1",
            client_id="c1",
            redirect_uri="http://localhost:1/cb",
            code_challenge=challenge,
            code_challenge_method="S256",
            scopes=[],
            idp_tokens={
                "access_token": "ua",
                "refresh_token": "ur",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
            expires_at=now + 300,
            created_at=now,
        ),
    )
    auth = {"client_id": "c1", "client_secret": "c1-secret"}
    upstream = AsyncMock()

    async def refresh(**_):
        await asyncio.sleep(0.05)
        return {
            "access_token": "ua2",
            "refresh_token": "ur",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    upstream.refresh_token = AsyncMock(side_effect=refresh)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://proxy.test"
        ) as h:
            form = {
                **auth,
                "grant_type": "authorization_code",
                "code": "code1",
                "redirect_uri": "http://localhost:1/cb",
                "code_verifier": verifier,
            }
            r = await asyncio.gather(*(h.post("/token", data=form) for _ in range(2)))
            code_status = sorted(x.status_code for x in r)
            tokens = [x.json() for x in r if x.status_code == 200][0]
            with patch.object(
                proxy, "_create_upstream_oauth_client", return_value=upstream
            ):
                rf = {
                    **auth,
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                }
                r2 = await asyncio.gather(
                    *(h.post("/token", data=rf) for _ in range(2))
                )
            refresh_status = sorted(x.status_code for x in r2)
            fresh = [x.json() for x in r2 if x.status_code == 200][0]
    assert code_status == [200, 401] and refresh_status == [200, 401], (
        code_status,
        refresh_status,
    )
    stored = await proxy.get_client("c1")
    rt = await proxy.load_refresh_token(stored, fresh["refresh_token"])
    from mcp.server.auth.provider import AccessToken

    ok_upstream = AsyncMock(
        return_value=AccessToken(
            token="ua2", client_id="c1", scopes=[], expires_at=int(now) + 3600
        )
    )
    with patch.object(proxy._token_validator, "verify_token", ok_upstream):
        before = await proxy.load_access_token(fresh["access_token"]) is not None
        await proxy.revoke_token(rt)
        after = await proxy.load_access_token(fresh["access_token"]) is not None
    return code_status, refresh_status, before, after


flow = {}


def oauth_check():
    flow["r"] = asyncio.run(oauth_flow())
    return f"code {flow['r'][0]}, refresh {flow['r'][1]} (one redemption each under concurrency)"


record("CHECK", "oauth proxy flow", oauth_check)

# ---- PROBES: one per tightening / loosening / new default -------------------
record(
    "PROBE",
    "#6627 revoke refresh -> access token still valid?",
    lambda: (
        "n/a (oauth flow failed)"
        if "r" not in flow
        else f"before={flow['r'][2]} after={flow['r'][3]}"
    ),
)


async def cimd(valid_scopes, doc_scope, request):
    from fastmcp.server.auth.cimd import CIMDDocument

    proxy = (
        make_proxy(valid_scopes=valid_scopes)
        if valid_scopes is not None
        else make_proxy()
    )
    url = "https://client.example/cimd.json"
    doc = CIMDDocument(
        client_id=url,
        redirect_uris=["http://localhost:1/cb"],
        scope=doc_scope,
        token_endpoint_auth_method="none",
    )
    with patch.object(
        proxy._cimd_manager._fetcher, "fetch", AsyncMock(return_value=doc)
    ):
        c = await proxy._cimd_manager.get_client(url)
    try:
        granted = c.validate_scope(request)
    except Exception as e:
        granted = type(e).__name__
    return f"client.scope={c.scope!r}, request {request!r} -> {granted}"


record(
    "PROBE",
    "#5829 CIMD doc 'read admin', valid_scopes=['read']",
    lambda: cimd(["read"], "read admin", "admin"),
)
record(
    "PROBE",
    "#5829 CIMD doc 'openid', no scopes configured",
    lambda: cimd(None, "openid", "openid"),
)


def redirect():
    from fastmcp.server.auth.redirect_validation import matches_allowed_pattern as m

    return ", ".join(
        f"{u} vs {p} -> {m(u, p)}"
        for u, p in [
            ("https://a.example/cb?x=1", "https://a.example/cb"),
            ("http://localhost:5000/cb?x=1", "http://localhost:*/cb"),
            ("http://localhost:5000/cb#f", "http://localhost:*/cb"),
        ]
    )


record("PROBE", "#6000 redirect query/fragment", redirect)


def azure():
    from fastmcp.server.auth.providers.azure import AzureProvider

    a = AzureProvider(
        client_id="c",
        client_secret="s",
        tenant_id="organizations",
        base_url="http://localhost:8000",
        required_scopes=["read"],
        jwt_signing_key="x" * 32,
    )
    return f"expected issuer for tenant 'organizations' = {a._token_validator.issuer!r}"


record("PROBE", "#5694 Azure multi-tenant issuer check", azure)


async def caps(url):
    from fastmcp import Client

    async with Client(url) as c:
        cap = getattr(c, "server_capabilities", None)
        listen = getattr(c, "listen", None)
        if listen is None:
            return f"listChanged={cap.tools.list_changed if cap and cap.tools else None}, no Client.listen"
        try:
            async with c.listen(tools_list_changed=True) as sub:

                async def grow():
                    await asyncio.sleep(0.3)
                    async with Client(url) as c2:
                        await c2.call_tool("grow", {})

                t = asyncio.create_task(grow())
                ev = await asyncio.wait_for(sub.__aiter__().__anext__(), 10)
                await t
            got = type(ev).__name__
        except Exception as e:
            got = f"{type(e).__name__}"
        return f"listChanged={cap.tools.list_changed if cap and cap.tools else None}, listen -> {got}"


record(
    "PROBE", "#6551 listen, default", lambda: with_http(lambda u: asyncio.run(caps(u)))
)
record(
    "PROBE",
    "#6551 listen, FASTMCP_ENABLE_SUBSCRIPTIONS_LISTEN=false",
    lambda: with_http(
        lambda u: asyncio.run(caps(u)),
        env={"FASTMCP_ENABLE_SUBSCRIPTIONS_LISTEN": "false"},
    ),
)


def mirror_probe():
    return with_proxy(lambda u: asyncio.run(_run_mirror(u)))


async def _run_mirror(url):
    from fastmcp import Client
    from fastmcp.client.elicitation import ElicitResult

    async def elicit(*a):
        return ElicitResult(action="accept", content={"value": "x"})

    out = []
    for name, kw in [
        ("front without elicitation", {}),
        ("front with elicitation", {"elicitation_handler": elicit}),
    ]:
        async with Client(url, mode="legacy", **kw) as c:
            r = await c.call_tool("ask", {}, raise_on_error=False)
            out.append(f"{name}: {(r.content[0].text if r.content else '')[:60]!r}")
    return "; ".join(out)


record("PROBE", "#6516 proxy relays only what the front declared", mirror_probe)


def usage():
    from fastmcp.server.middleware import (
        usage as _u,
    )  # new module; ImportError on older releases

    try:
        _u.UsageMiddleware()
        return "constructed without a salt (unexpected)"
    except ValueError:
        return "opt-in; bare UsageMiddleware() raises ValueError (salt required)"


record("PROBE", "#6510 UsageMiddleware", usage)

EVENTS_SERVER = textwrap.dedent("""
    import sys
    from fastmcp import FastMCP
    from fastmcp.experimental.events import Event, EventBatch, EventPoll, EventsExtension
    mcp = FastMCP("events-smoke")
    events = EventsExtension(poll_interval_ms=200)
    mcp.add_extension(events)
    ALERTS = []
    @events.event(name="alert.fired", description="An alert fired")
    def alert_fired(poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            return EventBatch(cursor=str(len(ALERTS)))
        new = ALERTS[int(poll.cursor):]
        return EventBatch(events=[Event(data=a, event_id=a["id"]) for a in new], cursor=str(len(ALERTS)))
    @mcp.tool
    def fire() -> str:
        ALERTS.append({"id": str(len(ALERTS))}); return "fired"
    mcp.run(transport="http", port=int(sys.argv[1]), show_banner=False, log_level="warning")
""")
(WORK / "events_server.py").write_text(EVENTS_SERVER)


async def _events(url):
    from fastmcp import Client
    from fastmcp.experimental.events import (
        EventsClientExtension,
        list_events,
        subscribe_events,
    )

    out = []
    async with Client(url, extensions=[EventsClientExtension()]) as c:
        out.append("types=" + ",".join(e.name for e in await list_events(c)))
        for delivery in ("poll", "push"):
            sub = subscribe_events(c, "alert.fired", delivery=delivery)

            async def fire():
                await asyncio.sleep(0.5)
                async with Client(url) as c2:
                    await c2.call_tool("fire", {})

            t = asyncio.create_task(fire())
            async for ev in sub:
                out.append(f"{delivery}: event {ev.event_id}")
                break
            await t
    return "; ".join(out)


def events_probe():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-I", str(WORK / "events_server.py"), str(port)],
        cwd=WORK,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    try:
        return asyncio.run(
            asyncio.wait_for(_events(f"http://127.0.0.1:{port}/mcp"), 30)
        )
    finally:
        proc.terminate()
        proc.wait(5)


record("PROBE", "#6489 experimental Events (poll + push)", events_probe)


# ---- PROBES added for the security fixes (#6703-#6707) ----------------------
def redirect_root():
    from fastmcp.server.auth.redirect_validation import matches_allowed_pattern as m

    return ", ".join(
        f"{u} vs {p} -> {m(u, p)}"
        for u, p in [
            ("https://app.example/evil", "https://app.example/"),
            ("https://app.example/evil", "https://app.example/*"),
            ("http://localhost:5000/any", "http://localhost/"),
        ]
    )


record("PROBE", "#6703 exact root redirect pattern", redirect_root)

OPENAPI_SERVER = textwrap.dedent("""
    import json, sys, httpx2 as httpx
    from fastmcp import FastMCP
    spec = {"openapi": "3.0.0", "info": {"title": "echo", "version": "1"}, "paths": {"/echo": {"get": {
        "operationId": "echo", "parameters": [{"name": "sess", "in": "cookie", "schema": {"type": "string"}}],
        "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}}}}}
    def handler(req):
        return httpx.Response(200, json={"cookie": req.headers.get("cookie"),
            "x-api-key": req.headers.get("x-api-key"), "mcp": sorted(k for k in req.headers if k.startswith("mcp-"))})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    kw = {} if sys.argv[2] == "default" else {"forward_client_headers": False}
    FastMCP.from_openapi(spec, client=client, **kw).run(transport="http", port=int(sys.argv[1]), show_banner=False, log_level="warning")
""")
(WORK / "openapi_server.py").write_text(OPENAPI_SERVER)


async def _openapi(url):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async with Client(StreamableHttpTransport(url, headers={"x-api-key": "k1"})) as c:
        r = await c.call_tool("echo", {"sess": "a;admin=1"})
        return json.dumps(r.structured_content or r.data)


def openapi_probe(mode):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-I", str(WORK / "openapi_server.py"), str(port), mode],
        cwd=WORK,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if proc.poll() is not None:
                return (
                    "server refused to start: "
                    + (proc.stderr.read().strip().splitlines() or ["?"])[-1][:150]
                )
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        return asyncio.run(
            asyncio.wait_for(_openapi(f"http://127.0.0.1:{port}/mcp"), 30)
        )
    finally:
        proc.terminate()
        proc.wait(5)


record(
    "PROBE",
    "#6704/#6705 OpenAPI cookie 'a;admin=1' + forwarded headers, default",
    lambda: openapi_probe("default"),
)
record(
    "PROBE", "#6705 OpenAPI forward_client_headers=False", lambda: openapi_probe("off")
)


async def refresh_reuse():
    """Rotate a refresh token, then present the old one again after the grace window."""
    import httpx2 as httpx
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from fastmcp import FastMCP
    from fastmcp.server.auth.oauth_proxy import proxy as proxy_mod
    from fastmcp.server.auth.oauth_proxy.models import ClientCode

    proxy = make_proxy()
    app = FastMCP("reuse-smoke", auth=proxy).http_app()
    await proxy.register_client(
        OAuthClientInformationFull(
            client_id="c1",
            client_secret="c1-secret",
            redirect_uris=[AnyUrl("http://localhost:1/cb")],
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
        )
    )
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    now = time.time()
    await proxy._code_store.put(
        key="code1",
        value=ClientCode(
            code="code1",
            client_id="c1",
            redirect_uri="http://localhost:1/cb",
            code_challenge=challenge,
            code_challenge_method="S256",
            scopes=[],
            idp_tokens={
                "access_token": "ua",
                "refresh_token": "ur",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
            expires_at=now + 300,
            created_at=now,
        ),
    )
    auth = {"client_id": "c1", "client_secret": "c1-secret"}
    upstream = AsyncMock()
    upstream.refresh_token = AsyncMock(
        return_value={
            "access_token": "ua2",
            "refresh_token": "ur",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://proxy.test"
        ) as h:
            t0 = (
                await h.post(
                    "/token",
                    data={
                        **auth,
                        "grant_type": "authorization_code",
                        "code": "code1",
                        "redirect_uri": "http://localhost:1/cb",
                        "code_verifier": verifier,
                    },
                )
            ).json()
            with patch.object(
                proxy, "_create_upstream_oauth_client", return_value=upstream
            ):

                def rf(rt):
                    return h.post(
                        "/token",
                        data={
                            **auth,
                            "grant_type": "refresh_token",
                            "refresh_token": rt,
                        },
                    )

                t1 = (await rf(t0["refresh_token"])).json()
                with patch.object(
                    proxy_mod, "_REFRESH_REUSE_GRACE_SECONDS", 0.0, create=True
                ):
                    await asyncio.sleep(0.05)
                    reuse = (await rf(t0["refresh_token"])).status_code
                after = (await rf(t1["refresh_token"])).status_code
    return (
        f"reuse of rotated token -> {reuse}; newest refresh token afterwards -> {after}"
    )


record(
    "PROBE",
    "#6706 rotated refresh token reused after grace (grace patched to 0)",
    refresh_reuse,
)

record(
    "PROBE",
    "#6707 client_registration_ttl_seconds",
    lambda: (
        f"accepted, ttl={make_proxy(client_registration_ttl_seconds=60)._client_registration_ttl_seconds}"
    ),
)

print(f"fastmcp {FASTMCP_VERSION}")
for kind, name, outcome in results:
    print(f"{kind:5} {name}: {outcome}")
sys.exit(1 if any(k == "CHECK" and o.startswith("FAIL") for k, _, o in results) else 0)
