"""Shared fixtures for client task tests."""

import pytest

from fastmcp.utilities.tests import temporary_settings


@pytest.fixture(autouse=True)
async def enable_tasks():
    """Enable task protocol support for all client task tests."""
    with temporary_settings(enable_tasks=True):
        yield
