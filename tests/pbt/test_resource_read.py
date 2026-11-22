"""A read through a real server hands the function the values that were expanded.

The pure round-trip in test_uri_template.py skips everything between the
client and the template: the client's `AnyUrl`, the wire, the server's
template lookup and argument coercion. This property draws the same
templates, registers one on a FastMCP server, reads the expanded URI through
an in-memory Client, and requires the function to receive the drawn values.

Path-safety screening is disabled here: it refuses some values on purpose,
and this property is about transport fidelity, not that policy.
"""

import json
import re
from typing import Any

from hypothesis import event, given, note
from pydantic import AnyUrl, ValidationError

from fastmcp import Client, FastMCP
from fastmcp.resources.template import expand_uri_template

from .strategies import DrawnTemplate, uri_templates
from .test_uri_template import known_divergence


def _make_fn(t: DrawnTemplate):
    args = []
    for v in t.vars:
        ann = "list[str]" if v.kind in ("list", "exploded") else "str"
        if v.kind in ("query", "list", "exploded", "continuation"):
            # Query params must be optional; a query param the URI omits
            # falls back to this default.
            args.append(f"{v.name}: {ann} | None = None")
        else:
            args.append(f"{v.name}: {ann}")
    src = (
        f"def read({', '.join(args)}) -> str:\n"
        f"    return json.dumps({{{', '.join(f'{v.name!r}: {v.name}' for v in t.vars)}}})\n"
    )
    scope: dict[str, Any] = {"json": json}
    exec(src, scope)
    return scope["read"]


def wire_divergence(uri: str) -> str | None:
    """Name a known way the wire's `AnyUrl` rewrites an expanded URI.

    Each name is an entry in FINDINGS.md. Anything else that fails is new.
    """
    try:
        wire = str(AnyUrl(uri))
    except ValidationError:
        return "not-a-url"
    if wire == uri:
        return None
    authority = re.compile(r"[a-z]+://([^/?#]*)")
    before, after = authority.match(uri), authority.match(wire)
    if before and (not after or before.group(1) != after.group(1)):
        return "authority-rewritten"
    path = re.split(r"[?#]", uri.split("://", 1)[-1])[0]
    if any(seg in (".", "..") for seg in path.split("/")[1:]):
        return "dot-segment-removed"
    return "other-rewrite"


@given(uri_templates())
async def test_read_roundtrip(t: DrawnTemplate):
    for v in t.vars:
        event(f"kind={v.kind}")
    if known := known_divergence(t):
        event(f"known: {known}")
        return
    uri = expand_uri_template(t.text, t.params)
    note(f"template: {t.text}")
    note(f"params: {t.params}")
    note(f"uri: {uri}")

    mcp = FastMCP("pbt")
    mcp.resource(t.text, security=None)(_make_fn(t))

    divergence = wire_divergence(uri)
    event(f"wire: {divergence or 'unchanged'}")
    try:
        async with Client(mcp) as client:
            contents = await client.read_resource(uri)
    except Exception:
        if divergence in KNOWN:
            return
        raise
    got = json.loads(contents[0].text)
    note(f"function got: {got}")
    if got != t.params and divergence in KNOWN:
        return
    assert got == t.params, "the function received different values"


# Wire rewrites recorded in FINDINGS.md; "other-rewrite" is deliberately absent.
KNOWN = {"not-a-url", "authority-rewritten", "dot-segment-removed"}
