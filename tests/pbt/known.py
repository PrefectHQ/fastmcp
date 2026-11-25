"""Known findings: skip a shape only while its finding still reproduces."""

import os
from collections.abc import Callable

ASSUMED_FIXED = {f for f in os.environ.get("PBT_ASSUME_FIXED", "").split(",") if f}


def still_open(finding: str, probe: Callable[[], bool]) -> bool:
    """True while `probe` reproduces `finding` in this tree.

    A mutant that undoes a fix names the finding in `PBT_ASSUME_FIXED`, so the
    probe does not mistake the mutant for the open bug. A probe that raises
    counts as fixed: the property then checks the shape and reports what
    changed, instead of the import failing and taking collection down with it.
    """
    if finding in ASSUMED_FIXED:
        return False
    try:
        return bool(probe())
    except Exception:
        return False
