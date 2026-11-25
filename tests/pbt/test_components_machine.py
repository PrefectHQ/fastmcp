"""A stateful model of what a client sees on a FastMCP server.

Rules add, remove, enable and disable tools, prompts and resources on a parent
server and on a child mounted under the namespace `c`, and list and call them
through one in-memory Client. The model is a dict of registered components
plus the two servers' visibility rules, with the documented semantics
(docs/servers/versioning.mdx, docs/servers/visibility.mdx):

- a client sees, per identifier, the highest enabled version, and the call
  without a version runs it; `meta.fastmcp.versions` lists the enabled
  versions, highest first
- an explicit version runs exactly that version, or fails
- a rule matches when every criterion it names matches (tags: any overlap);
  later rules override earlier ones; a mounted child's own rules are final
  for hiding: the parent can hide more, not restore what the child hid
- mixing versioned and unversioned components under one identifier is refused
  and changes nothing; removing what does not exist raises and changes nothing
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import mcp_types
from hypothesis import event, note
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    precondition,
    rule,
)

from fastmcp import Client, FastMCP
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.tools import Tool
from fastmcp.utilities.versions import dedupe_with_versions

from .known import still_open


def _duplicate_versions_reproduce() -> bool:
    """Equal versions from two servers are listed twice in `versions` (#6612)."""
    twins = [Tool.from_function(lambda: "", name="t", version="1") for _ in range(2)]
    listed = dedupe_with_versions(twins, key_fn=lambda c: c.name)
    meta = listed[0].meta or {}
    return meta.get("fastmcp", {}).get("versions") == ["1", "1"]


def _hidden_twin_shadows() -> bool:
    """A hidden parent tool shadows the mounted tool listed in its place (#6611)."""
    parent, child = FastMCP("p"), FastMCP("c")
    parent.tool(name="t")(lambda: "parent")
    child.tool(name="t", tags={"x"})(lambda: "child")
    parent.mount(child)
    parent.enable(tags={"x"}, only=True)

    async def probe() -> bool:
        async with Client(parent) as client:
            listed = [t.name for t in await client.list_tools()]
            try:
                await client.call_tool("t", {})
            except Exception:
                return listed == ["t"]
            return False

    return asyncio.run(probe())


# Skip a known finding only while it reproduces, so the property checks the
# shape again once the fix lands. Undo mutants set PBT_ASSUME_FIXED.
DUPLICATE_VERSIONS_OPEN = still_open("#6612", _duplicate_versions_reproduce)
HIDDEN_TWIN_OPEN = still_open("#6611", _hidden_twin_shadows)

KINDS = ["tool", "prompt", "resource"]
IDENTS = {
    "tool": ["t0", "t1"],
    "prompt": ["p0", "p1"],
    "resource": ["res://r0", "res://r1"],
}
VERSIONS: list[str | None] = [None, "1", "2", "10"]
TAGS = ["x", "y"]
OWNERS = ["parent", "child"]


def exposed(kind: str, owner: str, ident: str, namespace: str | None = "c") -> str:
    if owner == "parent" or namespace is None:
        return ident
    if kind == "resource":
        return ident.replace("res://", "res://c/")
    return f"c_{ident}"


def vkey(version: str | None) -> tuple[int, ...]:
    return (int(version),) if version is not None else ()


@dataclass(frozen=True)
class Rule:
    enabled: bool
    names: frozenset[str] | None
    tags: frozenset[str] | None
    kinds: frozenset[str] | None
    match_all: bool = False

    def matches(self, kind: str, name: str, tags: frozenset[str]) -> bool:
        if self.match_all:
            return True
        if self.names is not None and name not in self.names:
            return False
        if self.tags is not None and not (self.tags & tags):
            return False
        return not (self.kinds is not None and kind not in self.kinds)


class ComponentMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        # (kind, owner, ident, version) -> tags
        self.comps: dict[tuple[str, str, str, str | None], frozenset[str]] = {}
        self.vis_rules: dict[str, list[Rule]] = {"parent": [], "child": []}
        self.client: Client | None = None
        self.page_size: int | None = None
        # Quiet the expected warnings from drawn histories; teardown restores
        # the level so later tests in the same process see their warnings.
        self.logger = logging.getLogger("fastmcp")
        self.log_level = self.logger.level
        self.logger.setLevel(logging.ERROR)

    def exposed(self, kind: str, owner: str, ident: str) -> str:
        return exposed(kind, owner, ident, self.namespace)

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def teardown(self) -> None:
        try:
            if self.client is not None:
                self.run(self.client.__aexit__(None, None, None))
        finally:
            self.loop.close()
            self.logger.setLevel(self.log_level)

    # ---- starting state -------------------------------------------------

    @initialize(
        page_size=st.sampled_from([None, 1, 2, 3]),
        namespace=st.sampled_from(["c", None]),
        via=st.sampled_from(["direct", "proxy"]),
        data=st.data(),
    )
    def start(
        self, page_size: int | None, namespace: str | None, via: str, data
    ) -> None:
        event(f"page_size={page_size}")
        event(f"namespace={namespace}")
        event(f"via={via}")
        self.page_size = page_size
        self.namespace = namespace
        self.parent = FastMCP("parent", list_page_size=page_size)
        self.child = FastMCP("child")
        self.parent.mount(self.child, namespace=namespace)
        self.servers = {"parent": self.parent, "child": self.child}
        front = self.parent
        if via == "proxy":
            # A proxy in front of the parent must look exactly like the parent.
            # Caching is off: the backend changes between steps, and a cached
            # proxy serves stale listings by design until it lists again.
            front = FastMCP("proxy", list_page_size=page_size)
            front.add_provider(
                ProxyProvider(lambda: ProxyClient(self.parent), cache_ttl=0)
            )
        client = Client(front)
        self.run(client.__aenter__())
        self.client = client
        # A drawn starting state: several identifiers, each versioned or not,
        # so multi-version and mounted reads happen from the first step.
        for kind in KINDS:
            for owner in OWNERS:
                for ident in IDENTS[kind]:
                    shape = data.draw(
                        st.sampled_from(["absent", "unversioned", "versioned"])
                    )
                    if shape == "unversioned":
                        self._apply_add(kind, owner, ident, None, frozenset())
                    elif shape == "versioned":
                        versions = data.draw(
                            st.frozensets(st.sampled_from(VERSIONS[1:]), min_size=1)
                        )
                        for v in sorted(versions, key=vkey):
                            self._apply_add(
                                kind,
                                owner,
                                ident,
                                v,
                                data.draw(st.frozensets(st.sampled_from(TAGS))),
                            )
        event(f"start: {min(len(self.comps), 12) // 4 * 4}+ components")

    def _apply_add(self, kind, owner, ident, version, tags) -> None:
        self._register(kind, owner, ident, version, tags)
        self.comps[(kind, owner, ident, version)] = tags

    # ---- model ----------------------------------------------------------

    def enabled(self, kind: str, owner: str, ident: str, tags: frozenset[str]) -> bool:
        # A mounted server lists only what its own rules leave enabled, so the
        # parent's rules can hide a child component but not bring one back
        # (FastMCPProvider lists through the child's `list_tools()`).
        on = True
        if owner == "child":
            for r in self.vis_rules["child"]:
                if r.matches(kind, ident, tags):
                    on = r.enabled
            if not on:
                return False
        for r in self.vis_rules["parent"]:
            if r.matches(kind, self.exposed(kind, owner, ident), tags):
                on = r.enabled
        return on

    def candidates(self, kind: str) -> dict[str, list[tuple[str | None, str]]]:
        """exposed name -> enabled (version, owner), in serving order.

        Highest version first; for equal versions the provider registered
        first wins, and the parent's own components come before any mount
        (docs/servers/composition.mdx#conflict-resolution).
        """
        view: dict[str, list[tuple[str | None, str]]] = {}
        for (k, owner, ident, version), tags in self.comps.items():
            if k == kind and self.enabled(k, owner, ident, tags):
                view.setdefault(self.exposed(k, owner, ident), []).append(
                    (version, owner)
                )
        for entries in view.values():
            entries.sort(key=lambda e: (vkey(e[0]), e[1] == "parent"), reverse=True)
        return view

    def expected_view(self, kind: str) -> dict[str, list[str | None]]:
        """exposed name -> enabled versions, highest first, each once."""
        return {
            name: list(dict.fromkeys(v for v, _o in entries))
            for name, entries in self.candidates(kind).items()
        }

    def mixed(self, kind: str, name: str) -> bool:
        """Versioned and unversioned under one name, from different servers.

        One server refuses the mix, but nothing checks across a mount without
        a namespace, so the listing and a versioned call can disagree. #6613 kept that
        behavior and warns about it instead.
        Hidden components count: they still take part in the lookup.
        """
        versioned = {
            v is None
            for (k, o, i, v) in self.comps
            if k == kind and self.exposed(k, o, i) == name
        }
        return len(versioned) > 1

    # ---- mutations ------------------------------------------------------

    def _register(self, kind, owner, ident, version, tags):
        server = self.servers[owner]
        label = f"{owner}:{ident}@{version}"

        def fn() -> str:
            return label

        if kind == "tool":
            server.tool(name=ident, version=version, tags=set(tags))(fn)
        elif kind == "prompt":
            server.prompt(name=ident, version=version, tags=set(tags))(fn)
        else:
            server.resource(ident, version=version, tags=set(tags))(fn)

    @rule(
        kind=st.sampled_from(KINDS),
        owner=st.sampled_from(OWNERS),
        data=st.data(),
        version=st.sampled_from(VERSIONS),
        tags=st.frozensets(st.sampled_from(TAGS)),
    )
    def add(self, kind, owner, data, version, tags):
        ident = data.draw(st.sampled_from(IDENTS[kind]))
        siblings = {
            v for (k, o, i, v) in self.comps if (k, o, i) == (kind, owner, ident)
        }
        mixes = bool(siblings) and ((version is None) != (None in siblings))
        try:
            self._register(kind, owner, ident, version, tags)
        except ValueError:
            assert mixes, "an add the model allows was refused"
            event("add: refused mix")
            return
        assert not mixes, "mixing versioned and unversioned was allowed"
        event(
            "add: replaced"
            if (kind, owner, ident, version) in self.comps
            else "add: new"
        )
        self.comps[(kind, owner, ident, version)] = tags

    @rule(
        kind=st.sampled_from(KINDS),
        owner=st.sampled_from(OWNERS),
        data=st.data(),
        version=st.sampled_from(VERSIONS),
    )
    def remove(self, kind, owner, data, version):
        ident = data.draw(st.sampled_from(IDENTS[kind]))
        lp = self.servers[owner].local_provider
        remove = {
            "tool": lp.remove_tool,
            "prompt": lp.remove_prompt,
            "resource": lp.remove_resource,
        }[kind]
        if version is None:
            doomed = [c for c in self.comps if c[:3] == (kind, owner, ident)]
        else:
            doomed = [c for c in self.comps if c == (kind, owner, ident, version)]
        try:
            remove(ident, version=version)
        except KeyError:
            assert not doomed, "a remove of something registered raised"
            event("remove: missing")
            return
        assert doomed, "a remove of nothing succeeded"
        event("remove: removed")
        for c in doomed:
            del self.comps[c]

    @rule(
        owner=st.sampled_from(OWNERS),
        enable=st.booleans(),
        data=st.data(),
        only=st.booleans(),
    )
    def visibility(self, owner, enable, data, only):
        kind_for_names = data.draw(st.sampled_from(KINDS))
        pool = [
            self.exposed(kind_for_names, o, i) if owner == "parent" else i
            for o in (OWNERS if owner == "parent" else ["child"])
            for i in IDENTS[kind_for_names]
        ]
        names = data.draw(st.none() | st.frozensets(st.sampled_from(pool), min_size=1))
        tags = data.draw(st.none() | st.frozensets(st.sampled_from(TAGS), min_size=1))
        kinds = data.draw(st.none() | st.frozensets(st.sampled_from(KINDS), min_size=1))
        if names is None and tags is None and kinds is None:
            names = frozenset(pool[:1])
        only = only and enable
        server = self.servers[owner]
        components = (
            {"template" if k == "resource" else k for k in kinds} if kinds else None
        )
        if components and "template" in components:
            components.add("resource")
        call = server.enable if enable else server.disable
        kwargs: dict[str, Any] = dict(
            names=set(names) if names else None,
            tags=set(tags) if tags else None,
            components=components,
        )
        if only:
            kwargs["only"] = True
            self.vis_rules[owner].append(Rule(False, None, None, None, match_all=True))
        call(**kwargs)
        self.vis_rules[owner].append(Rule(enable, names, tags, kinds))
        event(
            f"visibility: {owner} {'enable' if enable else 'disable'}{' only' if only else ''}"
        )

    # ---- reads ----------------------------------------------------------

    async def _list(self, kind: str) -> list[Any]:
        assert self.client is not None
        if kind == "tool":
            return await self.client.list_tools()
        if kind == "prompt":
            return await self.client.list_prompts()
        return await self.client.list_resources()

    @staticmethod
    def _ident(kind: str, item: Any) -> str:
        return str(item.uri) if kind == "resource" else item.name

    @rule(kind=st.sampled_from(KINDS))
    def list_all(self, kind):
        items = self.run(self._list(kind))
        got: dict[str, list[str | None]] = {}
        for item in items:
            ident = self._ident(kind, item)
            assert ident not in got, "a listing repeats an identifier"
            fm = (item.meta or {}).get("fastmcp", {})
            got[ident] = list(fm.get("versions") or [fm.get("version")])
            assert fm.get("version") == got[ident][0], (
                "the listing serves a version other than the highest"
            )
        want = self.expected_view(kind)
        note(f"{kind} listing: got {got}, want {want}")
        event(f"list {kind}: {min(len(want), 3)}+ visible")
        assert set(got) == set(want), "the listing shows a different set of components"
        for ident, versions in want.items():
            if self.mixed(kind, ident):
                event("known: #6613 cross-mount version mix")
                continue
            if DUPLICATE_VERSIONS_OPEN and got[ident] != versions:
                if list(dict.fromkeys(got[ident])) == versions:
                    event("known: #6612 equal versions listed twice")
                    continue
            assert got[ident] == versions, "the listing reports different versions"

    @precondition(lambda self: self.page_size is not None)
    @rule(kind=st.sampled_from(KINDS))
    def list_paged(self, kind):
        assert self.client is not None
        method = {
            "tool": self.client.list_tools_mcp,
            "prompt": self.client.list_prompts_mcp,
            "resource": self.client.list_resources_mcp,
        }[kind]
        attr = {"tool": "tools", "prompt": "prompts", "resource": "resources"}[kind]
        seen: list[str] = []
        cursor = None
        for _ in range(50):
            page = self.run(method(cursor=cursor))
            items = getattr(page, attr)
            assert len(items) <= (self.page_size or 0), (
                "a page is larger than list_page_size"
            )
            seen.extend(self._ident(kind, i) for i in items)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            raise AssertionError("pagination does not terminate")
        event(f"paged {kind}: {min(len(seen), 3)}+ items")
        assert len(seen) == len(set(seen)), "pages repeat an identifier"
        assert set(seen) == set(self.expected_view(kind)), (
            "pages miss or add components"
        )

    async def _call(self, kind: str, name: str, version: str | None) -> str:
        assert self.client is not None
        if kind == "tool":
            r = await self.client.call_tool(name, {}, version=version)
            return r.data
        if kind == "prompt":
            r = await self.client.get_prompt(name, version=version)
            content = r.messages[0].content
            assert isinstance(content, mcp_types.TextContent)
            return content.text
        r = await self.client.read_resource(name, version=version)
        assert isinstance(r[0], mcp_types.TextResourceContents)
        return r[0].text

    @rule(
        kind=st.sampled_from(KINDS), data=st.data(), version=st.sampled_from(VERSIONS)
    )
    def call(self, kind, data, version):
        registered = sorted({(o, i) for (k, o, i, _v) in self.comps if k == kind})
        if registered and data.draw(st.integers(0, 3)):
            owner, ident = data.draw(st.sampled_from(registered))
        else:
            owner = data.draw(st.sampled_from(OWNERS))
            ident = data.draw(st.sampled_from(IDENTS[kind]))
        name = self.exposed(kind, owner, ident)
        entries = self.candidates(kind).get(name, [])
        if self.mixed(kind, name):
            event("known: #6613 cross-mount version mix")
            return
        if version is None or entries[:1] and entries[0][0] is None:
            # No version asked, or only unversioned components: serve the
            # first in order. An unversioned component matches any requested
            # version (docs/servers/versioning.mdx, settled in #6582).
            pick = entries[0] if entries else None
            if version is not None and pick:
                event(f"explicit version on unversioned {kind}")
        else:
            pick = next((e for e in entries if e[0] == version), None)
        want = None
        if pick:
            v, o = pick
            want = f"{o}:{ident}@{v}"
            if HIDDEN_TWIN_OPEN and o == "child" and self.namespace is None:
                twin = (kind, "parent", ident, v)
                if twin in self.comps:
                    # The parent's equal, hidden twin answers the lookup first.
                    event("known: #6611 hidden twin shadows the mounted one")
                    return
        try:
            got = self.run(self._call(kind, name, version))
        except Exception as e:
            note(f"call {kind} {name}@{version} raised {e!r}"[:300])
            got = None
        event(f"call {kind}: {'ok' if want else 'error'} expected")
        note(f"call {kind} {name} version={version}: got {got!r}, want {want!r}")
        assert got == want, "a call ran a different component than the model"


TestComponents = ComponentMachine.TestCase
