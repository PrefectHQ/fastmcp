"""Configuration for server task tests.

Task tests require Docket infrastructure (Redis-backed task queue) which can
take significant time to initialize, especially under parallel test execution.
The default 5s timeout is too tight for these tests.
"""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Increase timeout for task tests that need Docket infrastructure."""
    for item in items:
        if not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(15))
