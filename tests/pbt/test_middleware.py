"""Middleware that sits between a client and the components it can use.

- `AuthMiddleware`: what `tools/list` (and the prompt and resource lists)
  shows a caller is what that caller can call, version for version, and a
  denied call names exactly the scopes the reachable chain still needs.
  The model is the documented semantics of `require_scopes` and
  `restrict_tag` (docs/servers/authorization.mdx) and of versions
  (docs/servers/versioning.mdx): a caller sees, per name, the highest
  version they may use, and a call without a version runs that one.
- `ResponseCachingMiddleware`: within its TTL, a cached server answers every
  request exactly as an uncached one does, for every caller and version.
- `RateLimitingMiddleware` and `SlidingWindowRateLimitingMiddleware`: under
  a fake clock, no client is ever admitted more than its budget.
"""

import asyncio
import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from hypothesis import event, given, note
from hypothesis import strategies as st
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.types import TextContent, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError, InsufficientScopeError
from fastmcp.server.auth import AccessToken, require_scopes, restrict_tag
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import AuthMiddleware, rate_limiting
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.middleware.rate_limiting import (
    RateLimitError,
    RateLimitingMiddleware,
    SlidingWindowRateLimitingMiddleware,
)

from .known import still_open

SCOPES = ["read", "write", "admin"]
TAGS = ["admin", "beta"]
NAMES = ["t0", "t1", "t2"]
VERSIONS = ["1", "2", "3"]
KINDS = ["tool", "prompt", "resource"]

# ---------------------------------------------------------------------------
# a drawn server: components, auth layers, callers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """`require_scopes(*scopes)` when `tag` is None, else `restrict_tag`."""

    scopes: frozenset[str]
    tag: str | None

    def build(self):
        if self.tag is None:
            return require_scopes(*sorted(self.scopes))
        return restrict_tag(self.tag, scopes=sorted(self.scopes))

    def applies(self, tags: frozenset[str]) -> bool:
        return self.tag is None or self.tag in tags

    def missing(self, tags: frozenset[str], scopes: frozenset[str] | None) -> set[str]:
        """Scopes this check still needs (docs: an absent token needs none
        named, it is denied outright)."""
        if not self.applies(tags) or scopes is None:
            return set()
        return set(self.scopes - scopes)

    def allows(self, tags: frozenset[str], scopes: frozenset[str] | None) -> bool:
        if not self.applies(tags):
            return True
        return scopes is not None and self.scopes <= scopes


checks = st.builds(
    Check,
    scopes=st.frozensets(st.sampled_from(SCOPES), min_size=1, max_size=2),
    tag=st.none() | st.sampled_from(TAGS),
)
# One or two AuthMiddleware layers, each holding one or two checks.
layers = st.lists(st.lists(checks, min_size=1, max_size=2), min_size=1, max_size=2)
# name -> {version (None = unversioned): tags}
catalogs = st.dictionaries(
    st.sampled_from(NAMES),
    st.one_of(
        st.frozensets(st.sampled_from(TAGS)).map(lambda t: {None: t}),
        st.dictionaries(
            st.sampled_from(VERSIONS),
            st.frozensets(st.sampled_from(TAGS)),
            min_size=1,
            max_size=3,
        ),
    ),
    min_size=1,
)
callers = st.none() | st.frozensets(st.sampled_from(SCOPES))


def ident(kind: str, name: str) -> str:
    return f"res://{name}" if kind == "resource" else name


def _constant(label: str):
    def fn() -> str:
        return label

    return fn


def build_server(kind: str, catalog, middleware) -> FastMCP:
    server = FastMCP("s", middleware=middleware)
    for name, versions in catalog.items():
        for version, tags in versions.items():
            label = f"{name}@{version}"
            fn = _constant(label)

            if kind == "tool":

                def tool(x: int = 0, label: str = label) -> str:
                    token = get_access_token()
                    who = sorted(token.scopes) if token else None
                    return f"{label}:{x}:{who}"

                server.tool(tool, name=name, version=version, tags=set(tags))
            elif kind == "prompt":
                server.prompt(name=name, version=version, tags=set(tags))(fn)
            else:
                server.resource(ident(kind, name), version=version, tags=set(tags))(fn)
    return server


def token_for(scopes: frozenset[str] | None) -> AuthenticatedUser | None:
    if scopes is None:
        return None
    access = AccessToken(
        token=f"token-{'-'.join(sorted(scopes))}",
        client_id="c",
        scopes=sorted(scopes),
        expires_at=None,
    )
    return AuthenticatedUser(access)


@contextmanager
def caller(scopes: frozenset[str] | None) -> Generator[None]:
    reset = auth_context_var.set(token_for(scopes))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def vkey(version: str | None) -> int:
    return int(version) if version is not None else -1


def allowed(layer_checks, tags, scopes) -> bool:
    return all(c.allows(tags, scopes) for layer in layer_checks for c in layer)


def served(catalog, layer_checks, name, scopes) -> str | None:
    """The version a caller sees listed, and runs without asking for one."""
    usable = [
        v for v, tags in catalog[name].items() if allowed(layer_checks, tags, scopes)
    ]
    return max(usable, key=vkey) if usable else None


# ---------------------------------------------------------------------------
# AuthMiddleware: listing and calling agree
# ---------------------------------------------------------------------------


async def _list(client: Client, kind: str) -> dict[str, str | None]:
    if kind == "resource":
        named = [
            (str(r.uri).removeprefix("res://"), r.meta)
            for r in await client.list_resources()
        ]
    else:
        items = await (client.list_tools() if kind == "tool" else client.list_prompts())
        named = [(item.name, item.meta) for item in items]
    return {
        name: (meta or {}).get("fastmcp", {}).get("version") for name, meta in named
    }


async def _use(client: Client, kind: str, name: str, version: str | None) -> str:
    if kind == "tool":
        result = await client.call_tool(name, {}, version=version)
        return result.data.split(":")[0]
    if kind == "prompt":
        result = await client.get_prompt(name, version=version)
        content = result.messages[0].content
        assert isinstance(content, TextContent)
        return content.text
    contents = await client.read_resource(ident(kind, name), version=version)
    assert isinstance(contents[0], TextResourceContents)
    return contents[0].text


def _denial(e: Exception) -> str:
    """Why AuthMiddleware refused, or re-raise: a crash is not a denial."""
    text = str(e)
    if "Authorization failed" not in text:
        raise e
    return "insufficient" if ": insufficient " in text else "hidden"


def _f13_probe() -> bool:
    """F13: AuthMiddleware lists an older version a caller may use, but an
    unversioned call is authorized against (and would run) the highest."""
    catalog = {"t0": {"1": frozenset(), "2": frozenset({"admin"})}}
    mw = [AuthMiddleware(auth=restrict_tag("admin", scopes=["read"]))]
    server = build_server("tool", catalog, mw)

    async def probe() -> bool:
        async with Client(server) as client:
            if await _list(client, "tool") != {"t0": "1"}:
                return False
            if await _use(client, "tool", "t0", "1") != "t0@1":
                return False
            try:
                await client.call_tool("t0", {})
            except Exception as e:
                return _denial(e) == "insufficient"
            return False

    with caller(None):
        return asyncio.run(probe())


F13_OPEN = still_open("F13", _f13_probe)


@given(
    kind=st.sampled_from(KINDS),
    catalog=catalogs,
    layer_checks=layers,
    scopes=callers,
)
def test_listing_and_calling_agree(kind, catalog, layer_checks, scopes):
    middleware = [
        AuthMiddleware(auth=[c.build() for c in layer]) for layer in layer_checks
    ]
    server = build_server(kind, catalog, middleware)
    note(f"catalog={catalog} layers={layer_checks} caller={scopes}")

    async def run() -> None:
        async with Client(server) as client:
            listed = await _list(client, kind)
            want = {
                name: v
                for name in catalog
                if (v := served(catalog, layer_checks, name, scopes)) is not None
                or None in catalog[name]
                and allowed(layer_checks, catalog[name][None], scopes)
            }
            note(f"listed {listed}, want {want}")
            assert set(listed) == set(want), "the listing shows other names"
            for name, version in want.items():
                assert listed[name] == version, "the listing serves another version"
            event(f"{kind}: {len(listed)}/{len(catalog)} listed")

            for name, versions in catalog.items():
                for asked in [None, *(v for v in versions if v is not None)]:
                    if asked is None:
                        expect = want.get(name, "denied")
                        expect = "denied" if name not in want else f"{name}@{expect}"
                    elif allowed(layer_checks, versions[asked], scopes):
                        expect = f"{name}@{asked}"
                    else:
                        expect = "denied"
                    why = None
                    try:
                        got = await _use(client, kind, name, asked)
                    except Exception as e:
                        note(f"{name}@{asked}: {type(e).__name__}: {e}"[:200])
                        why = _denial(e)
                        got = "denied"
                    top = max(versions, key=vkey)
                    if (
                        asked is None
                        and why == "insufficient"
                        and expect != "denied"
                        and expect != f"{name}@{top}"
                        and F13_OPEN
                    ):
                        event("known: F13")
                        continue
                    note(f"use {name}@{asked}: got {got}, want {expect}")
                    assert got == expect, "a call disagrees with the listing"

    with caller(scopes):
        asyncio.run(run())


async def _use_on_server(server: FastMCP, kind: str, name: str) -> None:
    if kind == "tool":
        await server.call_tool(name, {})
    elif kind == "prompt":
        await server.render_prompt(name)
    else:
        await server.read_resource(ident(kind, name))


@given(
    kind=st.sampled_from(KINDS),
    catalog=catalogs.filter(lambda c: all(None in v for v in c.values())),
    layer_checks=layers,
    scopes=st.frozensets(st.sampled_from(SCOPES)),
)
def test_a_denial_names_every_scope_the_chain_still_needs(
    kind, catalog, layer_checks, scopes
):
    """SEP-2350 step-up: a caller told the missing scopes, who then obtains
    them, is let through (no scope from a later layer left unnamed)."""
    middleware = [
        AuthMiddleware(auth=[c.build() for c in layer]) for layer in layer_checks
    ]
    server = build_server(kind, catalog, middleware)

    async def run() -> None:
        for name, versions in catalog.items():
            tags = versions[None]
            first_denying = next(
                (
                    i
                    for i, layer in enumerate(layer_checks)
                    if not all(c.allows(tags, scopes) for c in layer)
                ),
                None,
            )
            try:
                await _use_on_server(server, kind, name)
            except InsufficientScopeError as e:
                assert first_denying is not None, "an allowed call was refused"
                want = sorted(
                    {
                        s
                        for layer in layer_checks[first_denying:]
                        for c in layer
                        for s in c.missing(tags, scopes)
                    }
                )
                note(f"{name}: named {e.required_scopes}, want {want}")
                assert sorted(e.required_scopes) == want
                event(f"{kind}: named {len(want)} scopes")
                with caller(scopes | set(want)):
                    await _use_on_server(server, kind, name)
                continue
            except AuthorizationError:
                raise AssertionError("a scope-only denial gave no scopes") from None
            assert first_denying is None, "a denied call ran"

    with caller(scopes):
        asyncio.run(run())


# ---------------------------------------------------------------------------
# ResponseCachingMiddleware: invisible within its TTL
# ---------------------------------------------------------------------------

op = st.tuples(
    st.sampled_from(["list", "call"]),
    st.sampled_from(NAMES),
    st.none() | st.sampled_from(VERSIONS),
    st.integers(0, 2),
    callers,
)


@st.composite
def _twin(draw):
    """A call, then the same call with another version or argument: the
    pairs a cache key that drops either field would answer wrongly."""
    first = draw(op.map(lambda o: ("call", *o[1:])))
    _, name, version, x, scopes = first
    if draw(st.booleans()):
        version = draw(
            st.sampled_from([None, *VERSIONS]).filter(lambda v: v != version)
        )
    else:
        x = draw(st.integers(0, 2).filter(lambda v: v != x))
    return [first, ("call", name, version, x, scopes)]


ops = st.lists(op.map(lambda o: [o]) | _twin(), min_size=1, max_size=8).map(
    lambda chunks: [o for chunk in chunks for o in chunk]
)


@given(
    kind=st.sampled_from(KINDS),
    catalog=catalogs,
    layer_checks=st.none() | layers,
    order=st.sampled_from(["cache outside auth", "cache inside auth"]),
    history=ops,
)
def test_a_cached_server_answers_like_an_uncached_one(
    kind, catalog, layer_checks, order, history
):
    auth = [
        AuthMiddleware(auth=[c.build() for c in layer]) for layer in layer_checks or []
    ]
    cache = [ResponseCachingMiddleware()]
    cached_chain = cache + auth if order == "cache outside auth" else auth + cache
    plain = build_server(kind, catalog, auth)
    cached = build_server(kind, catalog, cached_chain)
    event(order)

    async def answer(server: FastMCP, op) -> str:
        what, name, version, x, scopes = op
        with caller(scopes):
            async with Client(server) as client:
                try:
                    if what == "list":
                        return repr(sorted((await _list(client, kind)).items()))
                    if kind == "tool":
                        r = await client.call_tool(name, {"x": x}, version=version)
                        return r.data
                    return await _use(client, kind, name, version)
                except Exception as e:
                    return f"error: {type(e).__name__}"

    async def run() -> None:
        for op in history:
            want = await answer(plain, op)
            got = await answer(cached, op)
            note(f"{op}: cached {got!r}, uncached {want!r}")
            event(f"{op[0]} {'error' if want.startswith('error') else 'ok'}")
            assert got == want, "the cache answered differently"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Rate limiting under a fake clock
# ---------------------------------------------------------------------------

client_id: contextvars.ContextVar[str] = contextvars.ContextVar("client_id")


@contextmanager
def fake_clock() -> Generator[list[float]]:
    now = [1000.0]
    with patch.object(rate_limiting, "time", SimpleNamespace(monotonic=lambda: now[0])):
        yield now


steps = st.lists(
    st.tuples(
        st.floats(0, 3, allow_nan=False).map(lambda d: round(d, 3)),
        st.sampled_from(["a", "b", "c"]),
    ),
    min_size=1,
    max_size=40,
)


def _drive(middleware, history) -> list[tuple[float, str]]:
    """Admitted (time, client) pairs for a history of (wait, client) steps."""
    server = FastMCP("s", middleware=[middleware])
    server.tool(name="t")(lambda: "ok")
    admitted: list[tuple[float, str]] = []

    async def run(now: list[float]) -> None:
        for wait, who in history:
            now[0] += wait
            reset = client_id.set(who)
            try:
                await server.call_tool("t", {})
                admitted.append((now[0], who))
            except RateLimitError:
                pass
            finally:
                client_id.reset(reset)

    with fake_clock() as now:
        asyncio.run(run(now))
    return admitted


@given(
    rate=st.sampled_from([0.5, 1.0, 2.0, 5.0]),
    burst=st.none() | st.integers(1, 4),
    per_client=st.booleans(),
    history=steps,
)
def test_token_bucket_admits_no_more_than_burst_plus_rate(
    rate, burst, per_client, history
):
    mw = RateLimitingMiddleware(
        max_requests_per_second=rate,
        burst_capacity=burst,
        get_client_id=(lambda ctx: client_id.get()) if per_client else None,
    )
    admitted = _drive(mw, history)
    capacity = mw.burst_capacity
    for who in {w for _t, w in admitted} if per_client else {None}:
        times = [t for t, w in admitted if who is None or w == who]
        for i, start in enumerate(times):
            for j in range(i, len(times)):
                allowed_count = capacity + rate * (times[j] - start) + 1e-9
                assert j - i + 1 <= allowed_count, (
                    f"{who} got {j - i + 1} requests in {times[j] - start}s"
                )
    event("all admitted" if len(admitted) == len(history) else "some refused")


@given(max_requests=st.integers(1, 4), per_client=st.booleans(), history=steps)
def test_sliding_window_admits_no_more_than_its_max(max_requests, per_client, history):
    mw = SlidingWindowRateLimitingMiddleware(
        max_requests=max_requests,
        window_minutes=1,
        get_client_id=(lambda ctx: client_id.get()) if per_client else None,
    )
    # One-minute windows need minute-scale waits to slide.
    stretched = [(wait * 20, who) for wait, who in history]
    admitted = _drive(mw, stretched)
    for who in {w for _t, w in admitted} if per_client else {None}:
        times = [t for t, w in admitted if who is None or w == who]
        for t in times:
            in_window = [u for u in times if t - 60 < u <= t]
            assert len(in_window) <= max_requests, f"{who}: {len(in_window)} in 60s"
    event("all admitted" if len(admitted) == len(history) else "some refused")
