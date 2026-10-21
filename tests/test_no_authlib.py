"""FastMCP's client and server extras must work without Authlib."""

import subprocess
import sys
import textwrap
from importlib.metadata import requires

import pytest
from packaging.requirements import Requirement


@pytest.mark.parametrize("extra", ["client", "server"])
def test_extra_does_not_require_authlib(extra: str):
    dependencies = [Requirement(value) for value in requires("fastmcp-slim") or []]
    active = [
        dependency.name.lower()
        for dependency in dependencies
        if dependency.marker is None or dependency.marker.evaluate({"extra": extra})
    ]
    assert "authlib" not in active


@pytest.mark.subprocess_heavy
def test_auth_imports_without_authlib():
    script = textwrap.dedent(
        """
        import sys

        class AuthlibBlocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "authlib":
                    raise ImportError("FastMCP must not require Authlib")
                return None

        sys.meta_path.insert(0, AuthlibBlocker())

        from fastmcp.client.auth import OAuth
        from fastmcp.server.auth import OAuthProxy, OIDCProxy, JWTVerifier
        from fastmcp.server.auth.providers.google import GoogleProvider
        from fastmcp.server.auth.oauth_proxy.upstream import OAuthError

        assert OAuthError("invalid_grant").error == "invalid_grant"
        assert not any(name.split(".")[0] == "authlib" for name in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
