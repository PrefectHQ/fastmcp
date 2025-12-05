"""Shared fixtures for task tests."""

import pytest

from fastmcp.utilities.tests import temporary_settings


@pytest.fixture(autouse=True)
async def enable_docket_and_tasks():
    """Enable Docket and task protocol support for all task tests."""
    with temporary_settings(
        enable_docket=True,
        enable_tasks=True,
    ):
        # Verify both are enabled
        import fastmcp

        assert fastmcp.settings.enable_docket, "Docket should be enabled after fixture"
        assert fastmcp.settings.enable_tasks, "Tasks should be enabled after fixture"

        yield
