"""Configuration for client task tests."""

import secrets
from pathlib import Path

import pytest

from fastmcp.utilities.tests import temporary_settings


@pytest.fixture(autouse=True)
def isolate_settings_home(_settings_home_root: Path):
    """Task-local override of the repo-wide ``isolate_settings_home`` fixture.

    Docket configuration moved out of core ``Settings`` into
    ``fastmcp_tasks.settings.DocketSettings``, so the repo-wide fixture's
    ``docket__*`` kwargs no longer resolve against core settings. This
    override keeps the per-test settings-home isolation while dropping the
    removed docket kwargs.
    """
    test_home = _settings_home_root / secrets.token_hex(8)
    test_home.mkdir()

    with temporary_settings(home=test_home, client_disconnect_timeout=1):
        yield
