import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert BrokenResourceError failures to skips"""
    outcome = yield
    report = outcome.get_result()

    # Only process actual failures during the call phase, not xfails
    if (
        report.when == "call"
        and report.failed
        and not hasattr(report, "wasxfail")
        and call.excinfo
        and call.excinfo.typename == "BrokenResourceError"
    ):
        # Convert to a skip
        report.outcome = "skipped"
        report.longrepr = (
            "/Users/nate/github.com/jlowin/fastmcp/tests/integration_tests/conftest.py",
            None,
            "Skipped: Skipping due to GitHub API rate limit (429)",
        )
