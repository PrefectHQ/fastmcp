"""Hypothesis profiles and the `pbt` marker for property-based tests.

`HYPOTHESIS_PROFILE=ci` (the default) keeps runs fast and derandomized so CI
cannot flake. `HYPOTHESIS_PROFILE=campaign` is for bug hunting: many examples,
the example database on, and no per-test timeout. `HYPOTHESIS_PROFILE=mutants`
is what `mutants.py` runs under: random, no database, so seeds are independent.
"""

import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis.database import DirectoryBasedExampleDatabase

# The explain phase line-traces every shrunk failure; through a server and
# client that takes minutes, so it is off everywhere.
PHASES = [Phase.explicit, Phase.reuse, Phase.generate, Phase.target, Phase.shrink]

settings.register_profile(
    "ci",
    max_examples=50,
    derandomize=True,
    database=None,
    deadline=None,
    print_blob=True,
    phases=PHASES,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "campaign",
    max_examples=int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", "5000")),
    database=DirectoryBasedExampleDatabase(".hypothesis/examples"),
    deadline=None,
    print_blob=True,
    phases=PHASES,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
settings.register_profile(
    "mutants",
    max_examples=int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", "200")),
    database=None,
    deadline=None,
    phases=PHASES,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
PROFILE = os.environ.get("HYPOTHESIS_PROFILE", "ci")
settings.load_profile(PROFILE)


HERE = Path(__file__).parent


def pytest_collection_modifyitems(items):
    long_run = PROFILE != "ci"
    for item in items:
        if HERE in item.path.parents:
            # Integration, not unit: 50 examples through schema conversion or a
            # server and client take longer than the unit suite's 5s, and they
            # need not run on every platform in the matrix.
            item.add_marker(pytest.mark.pbt)
            item.add_marker(pytest.mark.integration)
            if long_run:
                item.add_marker(pytest.mark.timeout(0))
            else:
                item.add_marker(pytest.mark.timeout(120))
