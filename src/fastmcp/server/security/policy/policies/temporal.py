"""Time-based access control policy.

Restricts access to specific days of the week and hours of the day.

Example::

    from datetime import time

    policy = TimeBasedPolicy(
        allowed_days=frozenset({0, 1, 2, 3, 4}),  # Monday-Friday
        allowed_start_time=time(9, 0),
        allowed_end_time=time(17, 0),
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)

logger = logging.getLogger(__name__)


@dataclass
class TimeBasedPolicy:
    """Time window restriction policy.

    Attributes:
        allowed_days: Set of allowed weekdays (0=Monday, 6=Sunday).
            Defaults to all days.
        allowed_start_time: Earliest allowed time (inclusive).
        allowed_end_time: Latest allowed time (inclusive).
        utc_offset_hours: UTC offset for time comparison.
        policy_id: Unique identifier for this policy instance.
        version: Version string.
    """

    allowed_days: frozenset[int] = field(default_factory=lambda: frozenset(range(7)))
    allowed_start_time: time = field(default_factory=lambda: time(0, 0))
    allowed_end_time: time = field(default_factory=lambda: time(23, 59, 59))
    utc_offset_hours: int = 0
    policy_id: str = "time-based-policy"
    version: str = "1.0.0"

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Check if the request timestamp falls within the allowed window."""
        tz = timezone(timedelta(hours=self.utc_offset_hours))
        now = context.timestamp.astimezone(tz)

        weekday = now.weekday()
        current_time = now.time()

        if weekday not in self.allowed_days:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Day {weekday} not in allowed days {sorted(self.allowed_days)}",
                policy_id=self.policy_id,
            )

        # Handle overnight windows (e.g., 22:00 to 06:00)
        if self.allowed_start_time <= self.allowed_end_time:
            in_window = self.allowed_start_time <= current_time <= self.allowed_end_time
        else:
            in_window = (
                current_time >= self.allowed_start_time
                or current_time <= self.allowed_end_time
            )

        if not in_window:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=(
                    f"Time {current_time.strftime('%H:%M')} outside allowed window "
                    f"{self.allowed_start_time.strftime('%H:%M')}-{self.allowed_end_time.strftime('%H:%M')}"
                ),
                policy_id=self.policy_id,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Within allowed time window",
            policy_id=self.policy_id,
            constraints=[
                f"time_window:{self.allowed_start_time.strftime('%H:%M')}-{self.allowed_end_time.strftime('%H:%M')}"
            ],
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version
