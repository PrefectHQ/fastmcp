"""Formal verification support for SecureMCP policies.

Invariants are machine-checkable conditions that must hold true for a
policy to be considered valid. The InvariantVerifier protocol enables
pluggable verification backends (e.g., SMT solvers, custom checkers).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class InvariantSeverity(Enum):
    """Severity level for invariant violations."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Invariant:
    """A machine-checkable invariant for formal verification.

    Invariants express conditions that must always hold true within the
    policy system. They can be checked against audit logs, policy states,
    or runtime behavior.

    Attributes:
        id: Unique identifier for this invariant.
        description: Human-readable description of what the invariant checks.
        expression: A machine-parseable expression (implementation-defined).
        severity: How critical a violation of this invariant is.
        tags: Optional tags for grouping/filtering invariants.
    """

    id: str
    description: str
    expression: str
    severity: InvariantSeverity = InvariantSeverity.MEDIUM
    tags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class InvariantVerificationResult:
    """Result of verifying an invariant.

    Attributes:
        invariant: The invariant that was checked.
        satisfied: Whether the invariant holds.
        counter_example: If not satisfied, data showing the violation.
        verified_at: When the verification was performed.
        verifier_id: Identifier of the verifier that produced this result.
    """

    invariant: Invariant
    satisfied: bool
    counter_example: dict[str, Any] | None = None
    verified_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    verifier_id: str = "unknown"


@runtime_checkable
class InvariantVerifier(Protocol):
    """Protocol for invariant verification backends.

    Implementations can be synchronous or asynchronous.
    """

    def verify(
        self, invariant: Invariant, context: dict[str, Any]
    ) -> InvariantVerificationResult | Awaitable[InvariantVerificationResult]: ...

    def get_verifier_id(self) -> str | Awaitable[str]: ...


class ExpressionInvariantVerifier:
    """A simple invariant verifier that evaluates Python expressions.

    The expression is evaluated in a restricted namespace with the
    provided context. This is suitable for basic invariant checking
    but NOT for untrusted input.

    Warning:
        Only use with trusted invariant expressions from your own
        policy definitions. Never evaluate expressions from external
        sources.
    """

    def __init__(self, verifier_id: str = "expression-verifier") -> None:
        self._verifier_id = verifier_id

    def get_verifier_id(self) -> str:
        return self._verifier_id

    def verify(
        self, invariant: Invariant, context: dict[str, Any]
    ) -> InvariantVerificationResult:
        """Verify an invariant by evaluating its expression.

        Args:
            invariant: The invariant to check.
            context: Variables available to the expression.

        Returns:
            Verification result indicating whether the invariant holds.
        """
        try:
            # Evaluate in restricted namespace
            namespace: dict[str, Any] = {
                "__builtins__": {
                    "len": len,
                    "all": all,
                    "any": any,
                    "min": min,
                    "max": max,
                    "sum": sum,
                    "abs": abs,
                    "isinstance": isinstance,
                    "str": str,
                    "int": int,
                    "float": float,
                    "bool": bool,
                    "list": list,
                    "dict": dict,
                    "set": set,
                    "tuple": tuple,
                    "True": True,
                    "False": False,
                    "None": None,
                },
            }
            namespace.update(context)

            # Evaluate expression
            result = eval(invariant.expression, namespace)  # noqa: S307

            return InvariantVerificationResult(
                invariant=invariant,
                satisfied=bool(result),
                counter_example=None if result else {"context": context},
                verifier_id=self._verifier_id,
            )

        except Exception as e:
            logger.warning(
                "Invariant verification failed for %s: %s",
                invariant.id,
                e,
            )
            return InvariantVerificationResult(
                invariant=invariant,
                satisfied=False,
                counter_example={"error": str(e)},
                verifier_id=self._verifier_id,
            )


class InvariantRegistry:
    """Registry for managing and checking invariants.

    Coordinates between invariants and verifiers, providing bulk
    verification and result tracking.
    """

    def __init__(self, verifier: InvariantVerifier | None = None) -> None:
        self._invariants: dict[str, Invariant] = {}
        self._verifier = verifier or ExpressionInvariantVerifier()
        self._results: list[InvariantVerificationResult] = []

    def register(self, invariant: Invariant) -> None:
        """Register an invariant for tracking."""
        self._invariants[invariant.id] = invariant

    def unregister(self, invariant_id: str) -> Invariant | None:
        """Remove an invariant from the registry."""
        return self._invariants.pop(invariant_id, None)

    @property
    def invariants(self) -> list[Invariant]:
        """All registered invariants."""
        return list(self._invariants.values())

    async def verify_all(
        self, context: dict[str, Any]
    ) -> list[InvariantVerificationResult]:
        """Verify all registered invariants against the given context.

        Args:
            context: Data available for invariant evaluation.

        Returns:
            List of verification results for all invariants.
        """
        results: list[InvariantVerificationResult] = []
        for invariant in self._invariants.values():
            result = self._verifier.verify(invariant, context)
            if inspect.isawaitable(result):
                result = await result
            results.append(result)
            self._results.append(result)

        return results

    async def verify_one(
        self, invariant_id: str, context: dict[str, Any]
    ) -> InvariantVerificationResult:
        """Verify a single invariant by ID.

        Raises:
            KeyError: If the invariant ID is not registered.
        """
        invariant = self._invariants.get(invariant_id)
        if invariant is None:
            raise KeyError(f"Invariant not found: {invariant_id}")

        result = self._verifier.verify(invariant, context)
        if inspect.isawaitable(result):
            result = await result
        self._results.append(result)
        return result

    @property
    def recent_results(self) -> list[InvariantVerificationResult]:
        """Recent verification results (last 1000)."""
        return self._results[-1000:]

    def get_violations(self) -> list[InvariantVerificationResult]:
        """Get all recorded violations (unsatisfied invariants)."""
        return [r for r in self._results if not r.satisfied]
