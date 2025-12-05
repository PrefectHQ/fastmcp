"""Shared fixtures for client task tests."""

import pytest

from fastmcp.utilities.tests import temporary_settings


@pytest.fixture(autouse=True)
async def enable_docket_and_tasks():
    """Enable Docket and task protocol support for all client task tests."""
    with temporary_settings(
        enable_docket=True,
        enable_tasks=True,
    ):
        yield
