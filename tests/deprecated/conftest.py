"""Conftest for deprecated tests - suppresses deprecation warnings."""

import pytest


def pytest_collection_modifyitems(items):
    """Add filterwarnings marker to all tests in this directory."""
    for item in items:
        item.add_marker(pytest.mark.filterwarnings("ignore::DeprecationWarning"))
