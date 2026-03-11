"""Actor profiles for the Reflexive Core (Phase 11).

Tracks per-actor behavioral state: which resources and tools they
access, their operation history, and a composite threat score that
aggregates signals from drift events and detectors.

The :class:`ActorProfile` is the unit of state; the
:class:`ActorProfileManager` owns all profiles and provides
lookup/creation/scoring logic.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.reflexive.models import DriftEvent, DriftSeverity

# ---------------------------------------------------------------------------
# Threat score weights (how much each drift severity adds to the score)
# ---------------------------------------------------------------------------

DEFAULT_SEVERITY_WEIGHTS: dict[DriftSeverity, float] = {
    DriftSeverity.INFO: 0.0,
    DriftSeverity.LOW: 1.0,
    DriftSeverity.MEDIUM: 3.0,
    DriftSeverity.HIGH: 7.0,
    DriftSeverity.CRITICAL: 15.0,
}

# Score decays by this factor per second (half-life ≈ 10 minutes)
DEFAULT_DECAY_RATE: float = 0.001155  # ln(2) / 600


# ---------------------------------------------------------------------------
# ActorProfile
# ---------------------------------------------------------------------------


@dataclass
class ActorProfile:
    """Behavioral profile for a single actor (agent/user).

    Tracks the resources and tools an actor has accessed, records
    operation history, and maintains a time-decaying threat score.

    Attributes:
        actor_id: The agent this profile tracks.
        known_tools: Set of tool names this actor has invoked.
        known_resources: Set of resource URIs this actor has read.
        known_prompts: Set of prompt names this actor has used.
        operation_history: Recent (metric_name, timestamp) pairs
            kept in a bounded deque for pattern detection.
        drift_events: Drift events attributed to this actor.
        raw_threat_score: Accumulated score before decay.
        last_score_update: Monotonic timestamp of the last score bump.
        first_seen: When this actor was first observed.
        last_seen: When this actor was last observed.
        metadata: Arbitrary extra data.
    """

    actor_id: str = ""
    known_tools: set[str] = field(default_factory=set)
    known_resources: set[str] = field(default_factory=set)
    known_prompts: set[str] = field(default_factory=set)
    operation_history: deque[tuple[str, float]] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    drift_events: list[DriftEvent] = field(default_factory=list)
    raw_threat_score: float = 0.0
    last_score_update: float = field(default_factory=time.monotonic)
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def scope_size(self) -> int:
        """Total number of distinct resources/tools/prompts accessed."""
        return (
            len(self.known_tools) + len(self.known_resources) + len(self.known_prompts)
        )

    @property
    def operation_count(self) -> int:
        """Number of operations in the history buffer."""
        return len(self.operation_history)


# ---------------------------------------------------------------------------
# ActorProfileManager
# ---------------------------------------------------------------------------


class ActorProfileManager:
    """Manages actor profiles and computes threat scores.

    Creates profiles on first observation and provides methods to
    record scope access, compute threat scores, and detect scope
    expansion.

    Example::

        mgr = ActorProfileManager()

        # Record tool access
        is_new = mgr.record_tool_access("agent-1", "dangerous_tool")
        if is_new:
            print("agent-1 accessed a new tool!")

        # Record a drift event and update threat score
        mgr.record_drift("agent-1", drift_event)

        # Check current threat score (decays over time)
        score = mgr.threat_score("agent-1")

    Args:
        severity_weights: Map of DriftSeverity to score increment.
        decay_rate: Exponential decay rate per second.
        history_size: Max operations kept per actor profile.
    """

    def __init__(
        self,
        *,
        severity_weights: dict[DriftSeverity, float] | None = None,
        decay_rate: float = DEFAULT_DECAY_RATE,
        history_size: int = 500,
    ) -> None:
        self._profiles: dict[str, ActorProfile] = {}
        self._severity_weights = severity_weights or dict(DEFAULT_SEVERITY_WEIGHTS)
        self._decay_rate = decay_rate
        self._history_size = history_size

    def get_or_create(self, actor_id: str) -> ActorProfile:
        """Get an existing profile or create a new one."""
        if actor_id not in self._profiles:
            self._profiles[actor_id] = ActorProfile(
                actor_id=actor_id,
                operation_history=deque(maxlen=self._history_size),
            )
        return self._profiles[actor_id]

    def get_profile(self, actor_id: str) -> ActorProfile | None:
        """Get an existing profile, or None."""
        return self._profiles.get(actor_id)

    @property
    def actor_count(self) -> int:
        """Number of tracked actors."""
        return len(self._profiles)

    def all_profiles(self) -> list[ActorProfile]:
        """Return all profiles."""
        return list(self._profiles.values())

    # ── Scope tracking ────────────────────────────────────────────────

    def record_tool_access(self, actor_id: str, tool_name: str) -> bool:
        """Record that an actor invoked a tool.

        Returns True if this is a *new* tool for the actor (scope expansion).
        """
        profile = self.get_or_create(actor_id)
        profile.last_seen = datetime.now(timezone.utc)
        is_new = tool_name not in profile.known_tools
        profile.known_tools.add(tool_name)
        profile.operation_history.append(("tool:" + tool_name, time.monotonic()))
        return is_new

    def record_resource_access(self, actor_id: str, resource_uri: str) -> bool:
        """Record that an actor read a resource.

        Returns True if this is a *new* resource for the actor.
        """
        profile = self.get_or_create(actor_id)
        profile.last_seen = datetime.now(timezone.utc)
        is_new = resource_uri not in profile.known_resources
        profile.known_resources.add(resource_uri)
        profile.operation_history.append(("resource:" + resource_uri, time.monotonic()))
        return is_new

    def record_prompt_access(self, actor_id: str, prompt_name: str) -> bool:
        """Record that an actor used a prompt.

        Returns True if this is a *new* prompt for the actor.
        """
        profile = self.get_or_create(actor_id)
        profile.last_seen = datetime.now(timezone.utc)
        is_new = prompt_name not in profile.known_prompts
        profile.known_prompts.add(prompt_name)
        profile.operation_history.append(("prompt:" + prompt_name, time.monotonic()))
        return is_new

    def record_operation(self, actor_id: str, operation: str) -> None:
        """Record a generic operation for pattern tracking."""
        profile = self.get_or_create(actor_id)
        profile.last_seen = datetime.now(timezone.utc)
        profile.operation_history.append((operation, time.monotonic()))

    # ── Threat scoring ────────────────────────────────────────────────

    def record_drift(self, actor_id: str, event: DriftEvent) -> None:
        """Record a drift event and bump the actor's threat score."""
        profile = self.get_or_create(actor_id)
        profile.drift_events.append(event)
        weight = self._severity_weights.get(event.severity, 0.0)
        # Apply decay to existing score first, then add new weight
        now = time.monotonic()
        elapsed = now - profile.last_score_update
        profile.raw_threat_score = (
            profile.raw_threat_score * _decay(elapsed, self._decay_rate) + weight
        )
        profile.last_score_update = now

    def threat_score(self, actor_id: str) -> float:
        """Get the current time-decayed threat score for an actor.

        Returns 0.0 if the actor has no profile.
        """
        profile = self._profiles.get(actor_id)
        if profile is None:
            return 0.0
        now = time.monotonic()
        elapsed = now - profile.last_score_update
        return profile.raw_threat_score * _decay(elapsed, self._decay_rate)

    def scope_expansion_rate(self, actor_id: str, window_seconds: float = 300.0) -> int:
        """Count how many new scope items were accessed in the last window.

        Looks at the operation history and counts entries that were
        the actor's first access to that resource/tool/prompt.

        This is an approximation based on the bounded history buffer.
        """
        profile = self._profiles.get(actor_id)
        if profile is None:
            return 0
        cutoff = time.monotonic() - window_seconds
        count = 0
        seen: set[str] = set()
        for op, ts in profile.operation_history:
            if ts < cutoff:
                continue
            if op not in seen:
                seen.add(op)
                count += 1
        return count

    def reset(self, actor_id: str | None = None) -> None:
        """Reset profiles.

        Args:
            actor_id: If provided, reset only this actor.
                If None, reset all profiles.
        """
        if actor_id is None:
            self._profiles.clear()
        else:
            self._profiles.pop(actor_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decay(elapsed_seconds: float, rate: float) -> float:
    """Exponential decay factor."""
    import math

    return math.exp(-rate * elapsed_seconds)
