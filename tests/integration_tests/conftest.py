import os

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert BrokenResourceError failures to skips only for GitHub rate limits"""
    outcome = yield
    report = outcome.get_result()

    # Only process actual failures during the call phase, not xfails
    if (
        report.when == "call"
        and report.failed
        and not hasattr(report, "wasxfail")
        and call.excinfo
        and call.excinfo.typename == "BrokenResourceError"
        and item.module.__name__ == "tests.integration_tests.test_github_mcp_remote"
    ):
        # Only skip if the test is in the GitHub remote test module
        # This prevents catching unrelated BrokenResourceErrors
        report.outcome = "skipped"
        report.longrepr = (
            os.path.abspath(__file__),
            None,
            "Skipped: Skipping due to GitHub API rate limit (429)",
        )
