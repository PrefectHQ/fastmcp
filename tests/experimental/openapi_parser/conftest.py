"""Shared fixtures for openapi_new utilities tests."""

import pytest

from fastmcp.utilities.tests import temporary_settings


@pytest.fixture(autouse=True)
def use_new_openapi_parser():
    with temporary_settings(experimental__enable_new_openapi_parser=True):
        yield
