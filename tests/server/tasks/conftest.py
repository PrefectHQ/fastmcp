"""Shared fixtures for task tests."""

import pytest

from fastmcp.utilities.tests import temporary_settings


@pytest.fixture(autouse=True)
async def enable_tasks():
    """Enable task protocol support for all task tests."""
    with temporary_settings(enable_tasks=True):
        # Verify enabled
        import fastmcp

        assert fastmcp.settings.enable_tasks, "Tasks should be enabled after fixture"

        yield
