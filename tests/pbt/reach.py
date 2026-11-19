"""Reach checks: which interactions a property's cases actually exercise.

A property declares the interactions a bug would need (a read through a
template, a cancelled call, a page cursor that outlives a removal) and marks
each case that reaches one. After the test, the conftest fixture prints the
fraction of cases that reached each interaction and, under the `campaign`
profile, fails the test when a required one is below `FLOOR`: a quiet run
over a generator that never reaches the interaction proves nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from hypothesis import event

FLOOR = 0.002
REGISTRY: list[Reach] = []


@dataclass
class Reach:
    test: str
    required: frozenset[str]
    cases: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    _this_case: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        REGISTRY.append(self)

    def case(self) -> None:
        """Start a case; call once per example (or state machine run)."""
        self.cases += 1
        self._this_case = set()

    def hit(self, name: str) -> None:
        event(f"reach: {name}")
        if name not in self._this_case:
            self._this_case.add(name)
            self.counts[name] += 1

    def fractions(self) -> dict[str, float]:
        names = self.required | set(self.counts)
        return {n: self.counts[n] / self.cases for n in sorted(names)}

    def missed(self) -> list[str]:
        return [n for n in sorted(self.required) if self.counts[n] / self.cases < FLOOR]

    def reset(self) -> None:
        self.cases = 0
        self.counts.clear()
        self._this_case = set()
