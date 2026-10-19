from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def skip_windows_acl_subprocess(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep deploy tests from launching PowerShell for every state access.

    On Windows each read or write of CLI state restricts its ACL through a
    fresh `powershell.exe`, so a single login test spawns a dozen of them. On
    contended CI runners that pushes ordinary tests past pytest-timeout, whose
    Windows thread method kills the whole xdist worker. Tests that exercise
    the real ACL request `real_windows_acl` instead.
    """
    if "real_windows_acl" in request.fixturenames:
        return
    monkeypatch.setattr(
        "fastmcp.cli.deploy.state._restrict_windows_access", lambda path: None
    )


@pytest.fixture
def real_windows_acl() -> None:
    """Opt out of `skip_windows_acl_subprocess` and run the real PowerShell ACL."""
