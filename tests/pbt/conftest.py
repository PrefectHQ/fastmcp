"""Hypothesis profiles and the `pbt` marker for property-based tests.

`HYPOTHESIS_PROFILE=ci` (the default) keeps runs fast and derandomized so CI
cannot flake. `HYPOTHESIS_PROFILE=campaign` is for bug hunting: many examples,
the example database on, and no per-test timeout. `HYPOTHESIS_PROFILE=mutants`
is what `mutants.py` runs under: random, no database, so seeds are independent.
"""

import os

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


def pytest_collection_modifyitems(items):
    long_run = PROFILE != "ci"
    for item in items:
        if "tests/pbt" in str(item.fspath):
            item.add_marker(pytest.mark.pbt)
            if long_run:
                item.add_marker(pytest.mark.timeout(0))
            else:
                # 50 examples through schema conversion or a server and client
                # take longer than the suite-wide 5s.
                item.add_marker(pytest.mark.timeout(120))


@pytest.fixture(autouse=True)
def reach_check(request):
    """Print each property's reach and, under `campaign`, fail on a hole."""
    from .reach import FLOOR, REGISTRY

    for r in REGISTRY:
        r.reset()
    yield
    for r in REGISTRY:
        if r.test != request.node.name or not r.cases:
            continue
        report = ", ".join(f"{n} {f:.1%}" for n, f in r.fractions().items())
        print(f"\nreach[{r.test}] over {r.cases} cases: {report}")
        if PROFILE == "campaign" and (missed := r.missed()):
            pytest.fail(
                f"{r.test} reached {missed} in under {FLOOR:.1%} of cases; "
                "the generator does not exercise them"
            )
