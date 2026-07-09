"""Tests for path-security screening of templated resource parameters.

Templated resources extract parameter values from request URIs and hand
them to the handler. `ResourceSecurity` screens those values (traversal,
absolute paths, null bytes) before the handler runs, defaults-on, at the
server's read chokepoint.

The screening is applied to the *raw* URI string reaching the server
(`FastMCP.read_resource(str)`), which is the path the JSON-RPC handler and
internal callers use. Over the in-memory `Client`, URIs are wrapped in
`AnyUrl`, which independently normalises many `..` payloads away before
they reach the server — a separate layer of defense.
"""

import pytest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ResourceSecurityError
from fastmcp.resources.security import (
    DEFAULT_RESOURCE_SECURITY,
    INHERIT_SECURITY,
    ResourceSecurity,
)


# ---------------------------------------------------------------------------
# ResourceSecurity model (unit)
# ---------------------------------------------------------------------------


class TestResourceSecurityModel:
    @pytest.mark.parametrize(
        "value",
        [
            "../etc/passwd",
            "..",
            "a/../../b",
            "nested/../../outside",
        ],
    )
    def test_rejects_traversal(self, value: str):
        assert ResourceSecurity().validate({"path": value}) == "path"

    @pytest.mark.parametrize(
        "value",
        [
            "/etc/passwd",
            "/absolute/injection",
            "C:\\Windows",
            "C:relative",
            "\\\\server\\share",
        ],
    )
    def test_rejects_absolute(self, value: str):
        assert ResourceSecurity().validate({"path": value}) == "path"

    @pytest.mark.parametrize(
        "value",
        [
            "a\x00b",
            "good\x00/../../../etc/passwd",
        ],
    )
    def test_rejects_null_bytes(self, value: str):
        assert ResourceSecurity().validate({"path": value}) == "path"

    @pytest.mark.parametrize(
        "value",
        [
            "HEAD~3..HEAD",
            "v1..v2",
            "a.b.c",
            "file.tar.gz",
            "1.0..2.0",
            ".env",
            ".git/config",
            "...",
            "docs/readme.txt",
            "foo/../bar",  # net depth stays >= 0 -> not an escape (SDK semantics)
            "café/naïve",
        ],
    )
    def test_allows_safe_values(self, value: str):
        """Dots inside a segment, benign relative paths, and dotfiles pass.

        This mirrors the SDK's component-based `contains_path_traversal`:
        only a standalone `..` segment counts as traversal. A leading-dot
        single segment (`.env`) is an ordinary name, not traversal, and
        passes default screening — filesystem exposure of such names is the
        handler's concern (e.g. via `safe_join` to a root), not this check.
        """
        assert ResourceSecurity().validate({"path": value}) is None

    def test_exempt_params_skipped(self):
        security = ResourceSecurity(exempt_params={"ref"})
        assert security.validate({"ref": "../anything"}) is None
        # A non-exempt param is still screened.
        assert security.validate({"path": "../x", "ref": "../y"}) == "path"

    def test_wildcard_segments_screened_element_wise(self):
        """List values (from wildcard {path*}) are screened per element."""
        assert ResourceSecurity().validate({"path": ["a", "..", "b"]}) == "path"
        assert ResourceSecurity().validate({"path": ["a", "b", "c"]}) is None

    def test_non_string_values_ignored(self):
        assert ResourceSecurity().validate({"n": 5, "flag": True}) is None

    def test_individual_checks_toggleable(self):
        no_traversal = ResourceSecurity(reject_path_traversal=False)
        assert no_traversal.validate({"path": "../x"}) is None
        # but absolute still rejected
        assert no_traversal.validate({"path": "/etc/passwd"}) == "path"

    def test_returns_first_failing_param_name(self):
        # dict order preserved; first failing name returned
        result = ResourceSecurity().validate({"safe": "ok", "bad": ".."})
        assert result == "bad"


# ---------------------------------------------------------------------------
# Enforcement at the server chokepoint (raw-string reads)
# ---------------------------------------------------------------------------


class TestChokepointEnforcement:
    @pytest.fixture
    def server(self) -> FastMCP:
        mcp = FastMCP("test")

        @mcp.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return f"content:{path}"

        return mcp

    @pytest.mark.parametrize(
        "uri",
        [
            "file:///../etc/passwd",
            "file:///a/../../b",
            "file:////etc/passwd",  # -> path param '/etc/passwd' (absolute)
            "file:///a\x00b",
        ],
    )
    async def test_traversal_rejected_by_default(self, server: FastMCP, uri: str):
        with pytest.raises(ResourceSecurityError):
            await server.read_resource(uri)

    @pytest.mark.parametrize(
        "uri",
        [
            "file:///docs/readme.txt",
            "file:///HEAD~3..HEAD",
            "file:///v1..v2",
            "file:///file.tar.gz",
            "file:///.env",
        ],
    )
    async def test_safe_uris_pass_by_default(self, server: FastMCP, uri: str):
        result = await server.read_resource(uri)
        assert "content:" in result.contents[0].content


class TestServerDefaultConfiguration:
    async def test_server_default_disabled(self):
        mcp = FastMCP("test", resource_security=None)

        @mcp.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return f"content:{path}"

        # Traversal passes when server-wide screening is disabled.
        result = await mcp.read_resource("file:///../etc/passwd")
        assert result.contents[0].content == "content:../etc/passwd"

    async def test_server_default_custom_exemption(self):
        mcp = FastMCP(
            "test",
            resource_security=ResourceSecurity(exempt_params={"path"}),
        )

        @mcp.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return f"content:{path}"

        result = await mcp.read_resource("file:///../etc/passwd")
        assert result.contents[0].content == "content:../etc/passwd"

    async def test_server_default_applies_to_all_templates(self):
        """A single server default screens every templated resource."""
        mcp = FastMCP("test")

        @mcp.resource("a://{path*}")
        def read_a(path: str) -> str:
            return path

        @mcp.resource("b://{path*}")
        def read_b(path: str) -> str:
            return path

        for scheme in ("a", "b"):
            with pytest.raises(ResourceSecurityError):
                await mcp.read_resource(f"{scheme}://../escape")


class TestPerComponentOverride:
    async def test_component_disable_overrides_server_default(self):
        mcp = FastMCP("test")  # default: screening on

        @mcp.resource("git://diff/{ref}", security=None)
        def git_diff(ref: str) -> str:
            return f"diff:{ref}"

        # '..' in the ref is allowed because this component disabled screening.
        result = await mcp.read_resource("git://diff/HEAD~3..HEAD")
        assert result.contents[0].content == "diff:HEAD~3..HEAD"

    async def test_component_exemption_overrides_server_default(self):
        mcp = FastMCP("test")

        @mcp.resource(
            "git://diff/{ref}",
            security=ResourceSecurity(exempt_params={"ref"}),
        )
        def git_diff(ref: str) -> str:
            return f"diff:{ref}"

        result = await mcp.read_resource("git://diff/..")
        assert result.contents[0].content == "diff:.."

    async def test_component_enables_over_disabled_server_default(self):
        """A per-component policy overrides a server default of None."""
        mcp = FastMCP("test", resource_security=None)

        @mcp.resource("file:///{path*}", security=ResourceSecurity())
        def read_file(path: str) -> str:
            return path

        with pytest.raises(ResourceSecurityError):
            await mcp.read_resource("file:///../etc/passwd")

    def test_inherit_default_on_template(self):
        mcp = FastMCP("test")

        @mcp.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return path

        template = mcp._local_provider._components[
            list(mcp._local_provider._components)[0]
        ]
        assert template.security is INHERIT_SECURITY
        assert template.resolve_security(DEFAULT_RESOURCE_SECURITY) is (
            DEFAULT_RESOURCE_SECURITY
        )


# ---------------------------------------------------------------------------
# End-to-end through the in-memory Client
# ---------------------------------------------------------------------------


class TestEndToEndClient:
    async def test_traversal_read_gets_clean_not_found(self):
        """A traversal attempt over the wire surfaces a non-leaky error.

        `resource://..` survives `AnyUrl` normalisation (the `..` sits in
        the authority, not the path), so it reaches the server chokepoint
        and is rejected. The client sees a generic "resource not found"
        error that never reveals the screening reason.
        """
        mcp = FastMCP("test")

        @mcp.resource("resource://{path*}")
        def read(path: str) -> str:
            return path

        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.read_resource("resource://..")

        message = str(exc_info.value)
        assert "not found" in message.lower()
        # Non-leaky: the error must not name the failing parameter or policy.
        assert "path" not in message.lower()
        assert "security" not in message.lower()

    async def test_legit_read_succeeds(self):
        mcp = FastMCP("test")

        @mcp.resource("file:///{path*}")
        def read(path: str) -> str:
            return f"content:{path}"

        async with Client(mcp) as client:
            result = await client.read_resource("file:///docs/readme.txt")

        assert result[0].text == "content:docs/readme.txt"


# ---------------------------------------------------------------------------
# Provider-sourced templates (mounted servers)
# ---------------------------------------------------------------------------


class TestProviderSourcedTemplates:
    """Templates surfaced by a provider route through the same chokepoint.

    Enforcement lives at the server read chokepoint, not in the decorator,
    so a mounted server's templates inherit the *parent* server's default
    policy and are screened before the request is delegated.
    """

    async def test_mounted_template_screened_by_parent_default(self):
        child = FastMCP("child")

        @child.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return f"child:{path}"

        parent = FastMCP("parent")
        parent.mount(child)

        with pytest.raises(ResourceSecurityError):
            await parent.read_resource("file:///../escape")

    async def test_mounted_template_safe_read_succeeds(self):
        child = FastMCP("child")

        @child.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return f"child:{path}"

        parent = FastMCP("parent")
        parent.mount(child)

        result = await parent.read_resource("file:///docs/ok.txt")
        assert result.contents[0].content == "child:docs/ok.txt"

    async def test_parent_default_screens_even_if_child_disabled(self):
        """The parent's policy applies even when the child disabled its own.

        Screening runs at each server's chokepoint. A traversal is caught by
        the parent before delegation regardless of the child's configuration.
        """
        child = FastMCP("child", resource_security=None)

        @child.resource("file:///{path*}")
        def read_file(path: str) -> str:
            return f"child:{path}"

        parent = FastMCP("parent")  # default screening on
        parent.mount(child)

        with pytest.raises(ResourceSecurityError):
            await parent.read_resource("file:///../escape")
