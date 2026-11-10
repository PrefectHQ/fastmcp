"""Check the API claims the upgrade guides make against the real APIs.

The other two doc tests cover code blocks: one executes them, one compares the
before/after pair. Neither looks at *prose*, and prose is where a migration
guide does most of its work — mapping tables, prompt checklists, and sentences
naming an attribute to use. Those claims went wrong repeatedly and in the same
way: an API was named without anyone checking it resolved.

So this file checks the claims mechanically:

- every ``ctx.<name>`` the guides tell a reader to *use* exists on the class
  they'd be using it on, and every one they name as removed really is gone
- every ``MCPServer`` constructor parameter appears somewhere in the SDK v2
  guide, so a newly added SDK argument can't quietly go unmapped
- the ``request_context`` attributes the guides route people to are real

Run:
    uv run pytest tests/docs/test_upgrade_guide_api_claims.py -v
"""

from __future__ import annotations

import inspect
import re
import warnings
from pathlib import Path
from typing import Any

import pytest

UPGRADE_DIR = Path("docs/getting-started/upgrading")


def _guide(name: str) -> str:
    return (UPGRADE_DIR / name).read_text("utf-8")


with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from mcp.server.mcpserver import MCPServer

    from fastmcp import Context as FastMCPContext


# Context attributes the guides may mention without them existing on FastMCP's
# Context, because the guide's whole point is that they are gone or moved. Each
# is asserted to genuinely be absent, so a name that later gains an
# implementation stops being listed as missing.
DOCUMENTED_AS_ABSENT = {
    "sample",
    "sample_step",
    "list_roots",
    "mcp_server",
    "headers",
    "protocol_version",
    "client_capabilities",
    "elicit_url",
    "close_standalone_sse_stream",
    "notify_tools_changed",
    "notify_resources_changed",
    "notify_prompts_changed",
    "notify_resource_updated",
    "params",
    "meta",
}


def test_absent_context_attributes_are_really_absent():
    """Names the guides describe as gone must not exist on FastMCP's Context.

    If one of these gains an implementation, the guides are now telling people
    to work around something that works, and this test says so.
    """
    resurrected = [
        n for n in sorted(DOCUMENTED_AS_ABSENT) if hasattr(FastMCPContext, n)
    ]
    assert not resurrected, (
        f"guides describe these as absent from fastmcp.Context, but they exist: {resurrected}"
    )


@pytest.mark.parametrize(
    "guide",
    sorted(p.name for p in UPGRADE_DIR.glob("*.mdx")),
)
def test_ctx_attributes_named_in_guides_exist(guide: str):
    """Every ``ctx.<name>`` in a guide either exists or is documented as absent."""
    referenced = set(re.findall(r"`ctx\.([a-z_]+)", _guide(guide)))
    unknown = {
        name
        for name in referenced
        if not hasattr(FastMCPContext, name) and name not in DOCUMENTED_AS_ABSENT
    }
    assert not unknown, (
        f"{guide} names ctx.{{{', '.join(sorted(unknown))}}}, which do not exist on "
        f"fastmcp.Context and are not in DOCUMENTED_AS_ABSENT"
    )


def test_request_context_attributes_the_guides_route_to_exist():
    """The guides send people to ``ctx.request_context`` for several attributes.

    ``FastMCPRequestContext`` resolves its attributes dynamically, so this is
    checked against a live request rather than the class.
    """
    import asyncio

    from fastmcp import Client, FastMCP

    mcp = FastMCP("probe")

    @mcp.tool
    async def probe(ctx: FastMCPContext) -> list[str]:
        rc = ctx.request_context
        return [n for n in ("request_id", "meta", "protocol_version") if hasattr(rc, n)]

    async def run() -> list[str]:
        async with Client(mcp) as client:
            return (await client.call_tool("probe", {})).data

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        present = asyncio.run(run())

    assert set(present) == {"request_id", "meta", "protocol_version"}


# Context methods the SDK v2 guide says are *genuinely* unchanged. Existence is
# not enough for that claim — a method present on both classes with a different
# signature is worse than a missing one, because the import swap compiles and
# fails at runtime. So these are compared signature-for-signature.
CLAIMED_SIGNATURE_COMPATIBLE = ["report_progress"]

# Present on both, but with signatures that differ. The guide must describe each
# migration rather than list it as carrying over; this pins the difference so a
# future SDK or FastMCP release that converges them shows up as a failure.
KNOWN_SIGNATURE_DIFFERENCES = ["log", "info", "debug", "warning", "error", "elicit"]


@pytest.mark.parametrize("method", CLAIMED_SIGNATURE_COMPATIBLE)
def test_methods_claimed_unchanged_have_identical_signatures(method: str):
    from mcp.server.mcpserver import Context as SDKContext

    sdk = inspect.signature(getattr(SDKContext, method))
    fastmcp = inspect.signature(getattr(FastMCPContext, method))
    assert str(sdk) == str(fastmcp), (
        f"the SDK v2 guide lists ctx.{method} as carrying over unchanged, but "
        f"the signatures differ:\n  SDK    : {sdk}\n  FastMCP: {fastmcp}"
    )


@pytest.mark.parametrize("method", KNOWN_SIGNATURE_DIFFERENCES)
def test_methods_with_known_signature_differences_still_differ(method: str):
    from mcp.server.mcpserver import Context as SDKContext

    sdk = inspect.signature(getattr(SDKContext, method))
    fastmcp = inspect.signature(getattr(FastMCPContext, method))
    assert str(sdk) != str(fastmcp), (
        f"ctx.{method} signatures now match; the guide's migration note for it "
        f"is stale and should be moved to the unchanged list"
    )


# SDK v1's `mcp.server.fastmcp.FastMCP.__init__` parameters. Hardcoded because
# v1 cannot be installed alongside v4 to introspect — read from the published
# mcp 1.20.0 wheel. Anything here that FastMCP 4 does not accept must appear in
# the v1 guide, since a reader following "it's one import change" hits it.
SDK_V1_CONSTRUCTOR_PARAMS = [
    "name", "instructions", "website_url", "icons", "auth_server_provider",
    "token_verifier", "event_store", "tools", "debug", "log_level", "host",
    "port", "mount_path", "sse_path", "message_path", "streamable_http_path",
    "json_response", "stateless_http", "warn_on_duplicate_resources",
    "warn_on_duplicate_tools", "warn_on_duplicate_prompts", "dependencies",
    "lifespan", "auth", "transport_security", "transport",
]  # fmt: skip


def test_sdk_v1_constructor_params_fastmcp_rejects_are_documented():
    """Every v1 keyword FastMCP 4 refuses must be named in the v1 guide.

    The guide's headline is that upgrading is a single import change. That is
    only honest if the constructor arguments it *doesn't* accept are spelled
    out, so nobody follows the headline into a ``TypeError``.
    """
    from fastmcp import FastMCP

    guide = _guide("from-mcp-sdk-v1.mdx")
    probe: dict[str, Any] = {
        "name": "s",
        "icons": None,
        "tools": None,
        "lifespan": None,
    }

    undocumented = []
    for param in SDK_V1_CONSTRUCTOR_PARAMS:
        if param == "name":
            continue
        kwargs: dict[str, Any] = {param: probe.get(param)}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                FastMCP("s", **kwargs)
            continue  # accepted, nothing to document
        except TypeError:
            pass
        except Exception:
            continue  # accepted the keyword, rejected the probe value
        shorthand = param.replace("warn_on_duplicate", "")
        if re.search(rf"`{re.escape(param)}[=`]", guide):
            continue
        if param.startswith("warn_on_duplicate") and re.search(
            rf"`{re.escape(shorthand)}[=`]", guide
        ):
            continue
        undocumented.append(param)

    assert not undocumented, (
        "SDK v1 FastMCP() parameters that FastMCP 4 rejects but from-mcp-sdk-v1.mdx "
        f"never mentions: {undocumented}"
    )


def test_every_mcpserver_constructor_param_is_mapped():
    """The SDK v2 guide claims an exhaustive constructor mapping — hold it to that.

    A parameter added to ``MCPServer`` upstream should fail here rather than
    reach a reader as an unmapped keyword that raises ``TypeError`` on FastMCP.
    """
    guide = _guide("from-mcp-sdk-v2.mdx")
    params = [
        p for p in inspect.signature(MCPServer.__init__).parameters if p != "self"
    ]

    unmapped = []
    for param in params:
        # `warn_on_duplicate_resources` is covered by the table's shorthand
        # "warn_on_duplicate_tools, _resources, _prompts".
        shorthand = param.replace("warn_on_duplicate", "")
        if re.search(rf"`{re.escape(param)}[=`]", guide):
            continue
        if param.startswith("warn_on_duplicate") and re.search(
            rf"`{re.escape(shorthand)}`", guide
        ):
            continue
        unmapped.append(param)

    assert not unmapped, (
        f"MCPServer constructor parameters not mentioned in from-mcp-sdk-v2.mdx: {unmapped}"
    )
