"""FastMCP must import without the legacy httpx/httpcore packages.

FastMCP depends on httpx2 exclusively; legacy httpx remains importable in the
test environment only as a transitive dependency of optional LLM SDKs. That
masks clean-install regressions: an accidental ``import httpx`` (directly or
via a third-party integration such as authlib's httpx client) passes CI but
breaks any install without those extras.

This test simulates the clean install by running a subprocess that blocks
legacy httpx imports at the meta-path level, then imports the modules that
have historically regressed. The defensive user-compat shim in
``fastmcp.server.server`` catches ImportError by design and must keep working
when httpx is absent.
"""

import subprocess
import sys
import textwrap

import pytest

_BLOCKER_SCRIPT = textwrap.dedent(
    """
    import sys

    class _LegacyHttpxBlocker:
        blocked = {"httpx", "httpcore", "pytest_httpx"}

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in self.blocked:
                raise ImportError(
                    f"{name} is blocked: FastMCP must not require legacy httpx"
                )
            return None

    sys.meta_path.insert(0, _LegacyHttpxBlocker())

    import fastmcp.cli.apps_dev
    import fastmcp.client.client
    import fastmcp.server.auth.oauth_proxy.proxy
    import fastmcp.server.server
    import fastmcp.utilities.openapi.director

    print("OK")
    """
)


@pytest.mark.subprocess_heavy
def test_fastmcp_imports_without_legacy_httpx():
    result = subprocess.run(
        [sys.executable, "-c", _BLOCKER_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Import failed with legacy httpx blocked:\n{result.stderr}"
    )
    assert "OK" in result.stdout
