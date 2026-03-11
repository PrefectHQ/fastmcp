"""Pluggable anomaly detectors for the Reflexive Core (Phase 11).

Provides composable detection strategies beyond sigma-based deviation:

* :class:`AnomalyDetector` — Protocol that all detectors implement.
* :class:`SlidingWindowDetector` — Time-bucketed rate detection
  (e.g., "more than 10 errors in 5 minutes").
* :class:`PatternDetector` — Operation sequence detection
  (e.g., list-then-access-each = enumeration/reconnaissance).

Detectors are registered on the :class:`BehavioralAnalyzer` and evaluated
on every ``observe()`` call. Each detector independently produces
:class:`DriftEvent` instances when its condition is met.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from fastmcp.server.security.reflexive.models import (
    DriftEvent,
    DriftSeverity,
    DriftType,
)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AnomalyDetector(Protocol):
    """Protocol for pluggable anomaly detection strategies.

    Implementations receive every observation and decide whether to
    emit drift events.  Detectors are stateful — they maintain whatever
    internal bookkeeping they need between calls.

    Example::

        class MyDetector:
            @property
            def detector_id(self) -> str:
                return "my-detector"

            def observe(
                self, actor_id: str, metric_name: str, value: float, **kwargs: Any,
            ) -> list[DriftEvent]:
                ...

            def reset(self, actor_id: str | None = None) -> None:
                ...
    """

    @property
    def detector_id(self) -> str:
        """Unique identifier for this detector instance."""
        ...

    def observe(
        self,
        actor_id: str,
        metric_name: str,
        value: float,
        **kwargs: Any,
    ) -> list[DriftEvent]:
        """Process an observation and return any detected anomalies.

        Args:
            actor_id: The agent being observed.
            metric_name: Name of the metric.
            value: The observed value.
            **kwargs: Additional context (timestamp, metadata, etc.).

        Returns:
            List of DriftEvent instances (empty if no anomaly).
        """
        ...

    def reset(self, actor_id: str | None = None) -> None:
        """Reset detector state.

        Args:
            actor_id: If provided, reset only for this actor.
                If None, reset all state.
        """
        ...


# ---------------------------------------------------------------------------
# SlidingWindowDetector
# ---------------------------------------------------------------------------


@dataclass
class WindowConfig:
    """Configuration for a single sliding-window rule.

    Attributes:
        metric_name: The metric this rule applies to (or ``"*"`` for all).
        window_seconds: Length of the sliding window.
        max_count: Maximum number of events allowed in the window.
        severity: Drift severity when the threshold is breached.
        drift_type: Category of drift to report.
        description_template: Format string for the event description.
            Available placeholders: ``{actor_id}``, ``{metric_name}``,
            ``{count}``, ``{max_count}``, ``{window_seconds}``.
    """

    metric_name: str = "*"
    window_seconds: float = 300.0
    max_count: int = 10
    severity: DriftSeverity = DriftSeverity.HIGH
    drift_type: DriftType = DriftType.FREQUENCY_SPIKE
    description_template: str = (
        "{count} events for '{metric_name}' in {window_seconds}s "
        "(limit: {max_count}) from actor {actor_id}"
    )


class SlidingWindowDetector:
    """Time-bucketed rate detector.

    Fires a :class:`DriftEvent` when the number of observations for a
    metric exceeds a threshold within a sliding time window.

    Example::

        detector = SlidingWindowDetector(rules=[
            WindowConfig(
                metric_name="error_rate",
                window_seconds=300,
                max_count=10,
                severity=DriftSeverity.HIGH,
            ),
        ])

        # Each call records a timestamp; drift fires when count > max_count
        events = detector.observe("agent-1", "error_rate", 1.0)

    Args:
        rules: List of window configurations.
        detector_id: Unique identifier.
        cooldown_seconds: Minimum time between firing the same rule
            for the same actor/metric combination.
    """

    def __init__(
        self,
        rules: list[WindowConfig] | None = None,
        *,
        detector_id: str = "sliding-window",
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._detector_id = detector_id
        self._rules = list(rules or [])
        self._cooldown_seconds = cooldown_seconds
        # actor_id -> metric_name -> deque of monotonic timestamps
        self._windows: dict[str, dict[str, deque[float]]] = defaultdict(
            lambda: defaultdict(deque)
        )
        # (actor_id, metric_name, rule_index) -> last fire time
        self._last_fired: dict[tuple[str, str, int], float] = {}

    @property
    def detector_id(self) -> str:
        return self._detector_id

    def add_rule(self, rule: WindowConfig) -> None:
        """Add a window rule at runtime."""
        self._rules.append(rule)

    @property
    def rules(self) -> list[WindowConfig]:
        """Current rules (read-only copy)."""
        return list(self._rules)

    def observe(
        self,
        actor_id: str,
        metric_name: str,
        value: float,
        **kwargs: Any,
    ) -> list[DriftEvent]:
        now = time.monotonic()
        self._windows[actor_id][metric_name].append(now)

        events: list[DriftEvent] = []
        for idx, rule in enumerate(self._rules):
            if rule.metric_name != "*" and rule.metric_name != metric_name:
                continue

            window = self._windows[actor_id][metric_name]
            cutoff = now - rule.window_seconds
            # Prune old entries
            while window and window[0] < cutoff:
                window.popleft()

            count = len(window)
            if count <= rule.max_count:
                continue

            # Check cooldown
            key = (actor_id, metric_name, idx)
            last = self._last_fired.get(key, 0.0)
            if (now - last) < self._cooldown_seconds:
                continue

            self._last_fired[key] = now
            desc = rule.description_template.format(
                actor_id=actor_id,
                metric_name=metric_name,
                count=count,
                max_count=rule.max_count,
                window_seconds=rule.window_seconds,
            )
            events.append(
                DriftEvent(
                    event_id=str(uuid.uuid4())[:12],
                    drift_type=rule.drift_type,
                    severity=rule.severity,
                    actor_id=actor_id,
                    description=desc,
                    observed_value=float(count),
                    baseline_value=float(rule.max_count),
                    deviation=float(count - rule.max_count),
                    metadata={
                        "metric_name": metric_name,
                        "detector": self._detector_id,
                        "window_seconds": rule.window_seconds,
                    },
                )
            )

        return events

    def reset(self, actor_id: str | None = None) -> None:
        if actor_id is None:
            self._windows.clear()
            self._last_fired.clear()
        else:
            self._windows.pop(actor_id, None)
            self._last_fired = {
                k: v for k, v in self._last_fired.items() if k[0] != actor_id
            }


# ---------------------------------------------------------------------------
# PatternDetector
# ---------------------------------------------------------------------------


@dataclass
class OperationPattern:
    """A sequence of operations that constitutes a suspicious pattern.

    Attributes:
        pattern_id: Unique identifier for this pattern.
        steps: Ordered list of (metric_name, value_predicate) pairs.
            A value predicate is a callable ``(float) -> bool`` that
            tests whether the observed value matches the step.  Use
            ``None`` to match any value.
        window_seconds: All steps must occur within this time window.
        severity: Drift severity when the full pattern is matched.
        drift_type: Category of drift to report.
        description: Human-readable description of the pattern.
    """

    pattern_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    steps: list[tuple[str, Any]] = field(default_factory=list)
    window_seconds: float = 120.0
    severity: DriftSeverity = DriftSeverity.HIGH
    drift_type: DriftType = DriftType.UNUSUAL_PATTERN
    description: str = "Suspicious operation pattern detected"


@dataclass
class _PatternProgress:
    """Tracks how far an actor has progressed through a pattern."""

    current_step: int = 0
    first_step_time: float = 0.0


class PatternDetector:
    """Operation sequence detector.

    Detects ordered sequences of metric observations that match a
    suspicious pattern (e.g., list-all-resources → access-each =
    enumeration attack).

    Example::

        detector = PatternDetector(patterns=[
            OperationPattern(
                steps=[
                    ("list_resources", None),
                    ("read_resource", None),
                    ("read_resource", None),
                ],
                window_seconds=60,
                severity=DriftSeverity.HIGH,
                description="Possible resource enumeration",
            ),
        ])

    Args:
        patterns: List of operation patterns to detect.
        detector_id: Unique identifier.
        cooldown_seconds: Minimum time between firing the same pattern
            for the same actor.
    """

    def __init__(
        self,
        patterns: list[OperationPattern] | None = None,
        *,
        detector_id: str = "pattern",
        cooldown_seconds: float = 120.0,
    ) -> None:
        self._detector_id = detector_id
        self._patterns = list(patterns or [])
        self._cooldown_seconds = cooldown_seconds
        # actor_id -> pattern_id -> _PatternProgress
        self._progress: dict[str, dict[str, _PatternProgress]] = defaultdict(dict)
        # (actor_id, pattern_id) -> last fire time
        self._last_fired: dict[tuple[str, str], float] = {}

    @property
    def detector_id(self) -> str:
        return self._detector_id

    def add_pattern(self, pattern: OperationPattern) -> None:
        """Add a pattern at runtime."""
        self._patterns.append(pattern)

    @property
    def patterns(self) -> list[OperationPattern]:
        """Current patterns (read-only copy)."""
        return list(self._patterns)

    def observe(
        self,
        actor_id: str,
        metric_name: str,
        value: float,
        **kwargs: Any,
    ) -> list[DriftEvent]:
        now = time.monotonic()
        events: list[DriftEvent] = []

        for pattern in self._patterns:
            pid = pattern.pattern_id
            progress = self._progress[actor_id].get(pid)

            if progress is None:
                progress = _PatternProgress()
                self._progress[actor_id][pid] = progress

            # Check if we've timed out
            if (
                progress.current_step > 0
                and (now - progress.first_step_time) > pattern.window_seconds
            ):
                # Reset — window expired
                progress.current_step = 0
                progress.first_step_time = 0.0

            if progress.current_step >= len(pattern.steps):
                # Already completed, wait for cooldown reset
                continue

            step_metric, step_predicate = pattern.steps[progress.current_step]
            if step_metric != metric_name:
                continue

            # Check value predicate
            if step_predicate is not None and not step_predicate(value):
                continue

            # Step matches
            if progress.current_step == 0:
                progress.first_step_time = now
            progress.current_step += 1

            # Check if pattern is complete
            if progress.current_step >= len(pattern.steps):
                # Check cooldown
                key = (actor_id, pid)
                last = self._last_fired.get(key, 0.0)
                if (now - last) < self._cooldown_seconds:
                    progress.current_step = 0
                    progress.first_step_time = 0.0
                    continue

                self._last_fired[key] = now
                events.append(
                    DriftEvent(
                        event_id=str(uuid.uuid4())[:12],
                        drift_type=pattern.drift_type,
                        severity=pattern.severity,
                        actor_id=actor_id,
                        description=pattern.description,
                        observed_value=float(progress.current_step),
                        baseline_value=0.0,
                        deviation=float(progress.current_step),
                        metadata={
                            "detector": self._detector_id,
                            "pattern_id": pid,
                            "steps_matched": progress.current_step,
                            "metric_name": metric_name,
                        },
                    )
                )

                # Reset progress for next detection cycle
                progress.current_step = 0
                progress.first_step_time = 0.0

        return events

    def reset(self, actor_id: str | None = None) -> None:
        if actor_id is None:
            self._progress.clear()
            self._last_fired.clear()
        else:
            self._progress.pop(actor_id, None)
            self._last_fired = {
                k: v for k, v in self._last_fired.items() if k[0] != actor_id
            }
