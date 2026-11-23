"""Hypothesis profiles and the `pbt` marker for property-based tests.

`HYPOTHESIS_PROFILE=ci` (the default) keeps runs fast and derandomized so CI
cannot flake. `HYPOTHESIS_PROFILE=campaign` is for bug hunting: many examples,
the example database on, and no per-test timeout. `HYPOTHESIS_PROFILE=mutants`
is what `mutants.py` runs under: random, no database, so seeds are independent.
"""

import os
import time
import unittest
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

# Under the campaign profile, `PBT_BUDGET_SECONDS` turns each test into a
# time-boxed hunt: the test reruns with a fresh seed until the budget is spent,
# so a run costs the same wall-clock time however fast or slow each property is.
# Every rerun replays the example database first, so a failure found once is
# tried again first on every later run.
BUDGET = (
    float(os.environ.get("PBT_BUDGET_SECONDS", "0")) if PROFILE == "campaign" else 0.0
)


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


_ORIGINAL = pytest.StashKey[settings]()


def _settings_holder(item: pytest.Item) -> tuple[object, str]:
    """Where Hypothesis reads this test's settings when it runs.

    Both places are fixed when the module is imported, so loading a profile
    later changes nothing: `@given` keeps them on the wrapped function, and a
    state machine's `TestCase` keeps them on the class.
    """
    assert isinstance(item, pytest.Function)
    if item.cls is not None and issubclass(item.cls, unittest.TestCase):
        return item.cls, "settings"
    # A `@given` method in a plain class is a bound method; the settings live
    # on the function under it.
    return getattr(item.obj, "__func__", item.obj), "_hypothesis_internal_use_settings"


def _budgeted(item: pytest.Item) -> bool:
    """A Hypothesis property or state machine whose run size can be set."""
    if not isinstance(item, pytest.Function):
        return False
    if item.cls is not None and issubclass(item.cls, unittest.TestCase):
        return isinstance(getattr(item.cls, "settings", None), settings)
    return getattr(item.obj, "is_hypothesis_test", False)


def _failed(item: pytest.Item) -> bool:
    # A unittest TestCase (a state machine) never raises from runtest(): its
    # failure is recorded on the item and reported after the call.
    return bool(getattr(item, "_excinfo", None))


def _set_examples(item: pytest.Item, examples: int) -> None:
    holder, attr = _settings_holder(item)
    setattr(holder, attr, settings(getattr(holder, attr), max_examples=examples))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> None:
    """Rerun a property with fresh seeds until its budget is spent.

    Each pass is one full Hypothesis run with a new seed. Passes start small
    and double while they are short next to the time left, and the last one,
    which pytest's own call runs, is sized to end near the deadline instead of
    overrunning it. A failure stops the loop and fails the test as usual.
    """
    if not BUDGET or HERE not in item.path.parents or not _budgeted(item):
        return
    if item.get_closest_marker("pbt_smoke"):
        return  # checks the harness itself; one pass is enough
    deadline = time.monotonic() + BUDGET
    holder, attr = _settings_holder(item)
    item.stash[_ORIGINAL] = getattr(holder, attr)
    chunk = 20
    passes = examples = 0
    while True:
        _set_examples(item, chunk)
        started = time.monotonic()
        item.runtest()
        took = time.monotonic() - started
        passes += 1
        examples += chunk
        if _failed(item):
            # The failure is recorded; pytest's own call would run the machine
            # again and record a second one, reported as a teardown error.
            item.runtest = lambda: None  # type: ignore[method-assign]
            return
        left = deadline - time.monotonic()
        if left < 2 * took:
            break
        # Each run has a fixed cost (setup, replaying the database), so grow
        # the run while it is short next to what is left.
        if took < left / 8:
            chunk = min(chunk * 2, 5_000)
    # pytest's own call is the last pass; size it to what still fits.
    final = max(1, int(chunk * left / max(took, 1e-3) * 0.8))
    _set_examples(item, final)
    item.user_properties.append(("pbt_passes", passes + 1))
    item.user_properties.append(("pbt_examples", examples + final))


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    original = item.stash.get(_ORIGINAL, None)
    if original is not None:
        holder, attr = _settings_holder(item)
        setattr(holder, attr, original)
