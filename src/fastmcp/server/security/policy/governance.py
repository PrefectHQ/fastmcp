"""Policy governance workflow for SecureMCP.

Implements a structured lifecycle for policy changes: propose → validate →
simulate → approve → deploy. Prevents unsafe policy changes from reaching
production without review.

Example::

    from fastmcp.server.security.policy.governance import PolicyGovernor

    governor = PolicyGovernor(engine=engine, validator=validator)

    # Propose a new policy
    proposal = governor.propose(
        new_provider=new_policy,
        author="security-team",
        description="Tighten admin access",
    )

    # Validate and simulate
    validated = await governor.validate_proposal(proposal.proposal_id)
    simulated = await governor.simulate_proposal(proposal.proposal_id, scenarios)

    # Approve and deploy
    governor.approve(proposal.proposal_id, approver="ciso")
    record = await governor.deploy(proposal.proposal_id)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.policy.provider import PolicyProvider
from fastmcp.server.security.policy.validator import (
    PolicyValidator,
    ValidationResult,
)

if TYPE_CHECKING:
    from fastmcp.server.security.policy.engine import PolicyEngine
    from fastmcp.server.security.policy.simulation import (
        Scenario,
        SimulationReport,
    )

logger = logging.getLogger(__name__)


class ProposalStatus(Enum):
    """Lifecycle state of a policy proposal."""

    DRAFT = "draft"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    SIMULATED = "simulated"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ProposalAction(Enum):
    """What kind of change the proposal makes."""

    ADD = "add"
    SWAP = "swap"
    REMOVE = "remove"


@dataclass(frozen=True)
class PolicyProposalEvent:
    """A single event in the lifecycle of a proposal."""

    event: str
    actor: str
    note: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Export as JSON-serializable dict."""
        return {
            "event": self.event,
            "actor": self.actor,
            "note": self.note,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PolicyProposal:
    """A proposed policy change awaiting review.

    Attributes:
        proposal_id: Unique identifier.
        action: What kind of change this is.
        new_provider: The provider to add or swap in (None for REMOVE).
        target_index: Index of provider to swap/remove (None for ADD).
        author: Who proposed the change.
        description: Human-readable description of the change.
        assigned_reviewer: Reviewer/admin currently owning the proposal.
        status: Current lifecycle state.
        created_at: When the proposal was created.
        validation_result: Result of schema/semantic validation.
        simulation_report: Result of dry-run simulation.
        approved_by: Who approved the deployment.
        approved_at: When approval was granted.
        deployed_at: When the change was deployed.
        deployment_record: The swap/add record from deployment.
        rejection_reason: Why the proposal was rejected.
    """

    proposal_id: str
    action: ProposalAction
    new_provider: PolicyProvider | None
    target_index: int | None
    author: str
    description: str
    base_version_number: int | None = None
    assigned_reviewer: str | None = None
    status: ProposalStatus = ProposalStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    validation_result: ValidationResult | None = None
    simulation_report: Any = None  # SimulationReport, avoid circular import
    approved_by: str | None = None
    approved_at: datetime | None = None
    deployed_at: datetime | None = None
    deployment_record: Any = None
    rejection_reason: str | None = None
    decision_trail: list[PolicyProposalEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Export as JSON-serializable dict."""
        data: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "action": self.action.value,
            "author": self.author,
            "description": self.description,
            "base_version_number": self.base_version_number,
            "assigned_reviewer": self.assigned_reviewer,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "deployed_at": self.deployed_at.isoformat() if self.deployed_at else None,
            "rejection_reason": self.rejection_reason,
            "decision_trail": [event.to_dict() for event in self.decision_trail],
        }
        if self.validation_result is not None:
            data["validation"] = self.validation_result.to_dict()
        if self.simulation_report is not None:
            data["simulation"] = self.simulation_report.to_dict()
        if self.new_provider is not None:
            data["new_provider_type"] = type(self.new_provider).__name__
        data["target_index"] = self.target_index
        return data


class PolicyGovernor:
    """Manages the policy change lifecycle.

    Coordinates validation, simulation, approval, and deployment of
    policy changes through a structured workflow.

    Args:
        engine: The PolicyEngine to apply changes to.
        validator: PolicyValidator for pre-deployment checks.
        require_simulation: If True (default), proposals must be simulated
            before approval.
        require_approval: If True (default), proposals must be explicitly
            approved before deployment.
    """

    def __init__(
        self,
        engine: PolicyEngine,
        *,
        validator: PolicyValidator | None = None,
        require_simulation: bool = True,
        require_approval: bool = True,
    ) -> None:
        self._engine = engine
        self._validator = validator or PolicyValidator()
        self.require_simulation = require_simulation
        self.require_approval = require_approval
        self._proposals: dict[str, PolicyProposal] = {}

    @property
    def proposals(self) -> list[PolicyProposal]:
        """All proposals, sorted by creation time."""
        return sorted(self._proposals.values(), key=lambda p: p.created_at)

    @property
    def pending_proposals(self) -> list[PolicyProposal]:
        """Proposals that haven't been deployed, rejected, or withdrawn."""
        terminal = {
            ProposalStatus.DEPLOYED,
            ProposalStatus.REJECTED,
            ProposalStatus.WITHDRAWN,
        }
        return [p for p in self.proposals if p.status not in terminal]

    def get_proposal(self, proposal_id: str) -> PolicyProposal | None:
        """Get a proposal by ID."""
        return self._proposals.get(proposal_id)

    # ── Propose ───────────────────────────────────────────────

    def propose_add(
        self,
        provider: PolicyProvider,
        *,
        author: str = "unknown",
        description: str = "",
    ) -> PolicyProposal:
        """Propose adding a new provider to the engine.

        Args:
            provider: The provider to add.
            author: Who proposed the change.
            description: Why this change is being made.

        Returns:
            A new PolicyProposal in DRAFT status.
        """
        proposal = PolicyProposal(
            proposal_id=str(uuid.uuid4()),
            action=ProposalAction.ADD,
            new_provider=provider,
            target_index=None,
            author=author,
            description=description,
            base_version_number=self._current_version_number(),
        )
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="proposed",
                actor=author,
                note=description,
            )
        )
        self._proposals[proposal.proposal_id] = proposal
        logger.info(
            "Policy proposal created: %s (ADD by %s)",
            proposal.proposal_id,
            author,
        )
        return proposal

    def propose_swap(
        self,
        index: int,
        new_provider: PolicyProvider,
        *,
        author: str = "unknown",
        description: str = "",
    ) -> PolicyProposal:
        """Propose swapping a provider at a specific index.

        Args:
            index: Index of the provider to replace.
            new_provider: The replacement provider.
            author: Who proposed the change.
            description: Why this change is being made.

        Returns:
            A new PolicyProposal in DRAFT status.
        """
        if index < 0 or index >= len(self._engine.providers):
            raise IndexError(
                f"Provider index {index} out of range "
                f"(0-{len(self._engine.providers) - 1})"
            )

        proposal = PolicyProposal(
            proposal_id=str(uuid.uuid4()),
            action=ProposalAction.SWAP,
            new_provider=new_provider,
            target_index=index,
            author=author,
            description=description,
            base_version_number=self._current_version_number(),
        )
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="proposed",
                actor=author,
                note=description,
            )
        )
        self._proposals[proposal.proposal_id] = proposal
        logger.info(
            "Policy proposal created: %s (SWAP index %d by %s)",
            proposal.proposal_id,
            index,
            author,
        )
        return proposal

    def propose_remove(
        self,
        index: int,
        *,
        author: str = "unknown",
        description: str = "",
    ) -> PolicyProposal:
        """Propose removing a provider at a specific index.

        Args:
            index: Index of the provider to remove.
            author: Who proposed the change.
            description: Why this change is being made.

        Returns:
            A new PolicyProposal in DRAFT status.
        """
        if index < 0 or index >= len(self._engine.providers):
            raise IndexError(
                f"Provider index {index} out of range "
                f"(0-{len(self._engine.providers) - 1})"
            )

        proposal = PolicyProposal(
            proposal_id=str(uuid.uuid4()),
            action=ProposalAction.REMOVE,
            new_provider=None,
            target_index=index,
            author=author,
            description=description,
            base_version_number=self._current_version_number(),
        )
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="proposed",
                actor=author,
                note=description,
            )
        )
        self._proposals[proposal.proposal_id] = proposal
        logger.info(
            "Policy proposal created: %s (REMOVE index %d by %s)",
            proposal.proposal_id,
            index,
            author,
        )
        return proposal

    # ── Validate ──────────────────────────────────────────────

    def validate_proposal(self, proposal_id: str) -> ValidationResult:
        """Run validation on a proposal.

        Checks the proposed provider set (after the change) for logical
        issues and contradictions.

        Args:
            proposal_id: ID of the proposal to validate.

        Returns:
            ValidationResult with findings.

        Raises:
            KeyError: If the proposal doesn't exist.
            ValueError: If the proposal is not in DRAFT status.
        """
        proposal = self._get_or_raise(proposal_id)
        self._raise_if_stale(proposal, action="validate")
        if proposal.status not in (
            ProposalStatus.DRAFT,
            ProposalStatus.VALIDATION_FAILED,
        ):
            raise ValueError(
                f"Cannot validate proposal in {proposal.status.value} status"
            )

        # Build the hypothetical provider list after the change
        hypothetical = list(self._engine.providers)

        if proposal.action == ProposalAction.ADD:
            assert proposal.new_provider is not None
            hypothetical.append(proposal.new_provider)
        elif proposal.action == ProposalAction.SWAP:
            assert proposal.new_provider is not None
            assert proposal.target_index is not None
            hypothetical[proposal.target_index] = proposal.new_provider
        elif proposal.action == ProposalAction.REMOVE:
            assert proposal.target_index is not None
            hypothetical.pop(proposal.target_index)

        result = self._validator.validate_providers(hypothetical)
        proposal.validation_result = result

        if result.valid:
            proposal.status = ProposalStatus.VALIDATED
            proposal.decision_trail.append(
                PolicyProposalEvent(
                    event="validated",
                    actor="policy-validator",
                    note="Validation passed.",
                )
            )
        else:
            proposal.status = ProposalStatus.VALIDATION_FAILED
            proposal.decision_trail.append(
                PolicyProposalEvent(
                    event="validation_failed",
                    actor="policy-validator",
                    note="Validation found blocking issues.",
                )
            )

        return result

    # ── Simulate ──────────────────────────────────────────────

    async def simulate_proposal(
        self,
        proposal_id: str,
        scenarios: list[Scenario],
    ) -> SimulationReport:
        """Run a dry-run simulation of the proposal.

        Evaluates scenarios against the hypothetical provider set
        after the proposed change.

        Args:
            proposal_id: ID of the proposal to simulate.
            scenarios: Test scenarios to evaluate.

        Returns:
            SimulationReport with per-scenario results.

        Raises:
            KeyError: If the proposal doesn't exist.
            ValueError: If the proposal hasn't passed validation.
        """
        from fastmcp.server.security.policy.simulation import simulate

        proposal = self._get_or_raise(proposal_id)
        self._raise_if_stale(proposal, action="simulate")
        if proposal.status not in (
            ProposalStatus.VALIDATED,
            ProposalStatus.SIMULATED,
        ):
            raise ValueError(
                f"Cannot simulate proposal in {proposal.status.value} status. "
                "Must pass validation first."
            )

        # Build hypothetical provider list
        hypothetical = list(self._engine.providers)

        if proposal.action == ProposalAction.ADD:
            assert proposal.new_provider is not None
            hypothetical.append(proposal.new_provider)
        elif proposal.action == ProposalAction.SWAP:
            assert proposal.new_provider is not None
            assert proposal.target_index is not None
            hypothetical[proposal.target_index] = proposal.new_provider
        elif proposal.action == ProposalAction.REMOVE:
            assert proposal.target_index is not None
            hypothetical.pop(proposal.target_index)

        report = await simulate(
            hypothetical,
            scenarios,
            fail_closed=self._engine.fail_closed,
        )
        proposal.simulation_report = report
        proposal.status = ProposalStatus.SIMULATED
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="simulated",
                actor="policy-simulator",
                note=f"Ran {report.total} scenarios.",
            )
        )
        return report

    # ── Approve / Reject ──────────────────────────────────────

    def assign(
        self,
        proposal_id: str,
        *,
        reviewer: str,
        actor: str = "unknown",
        note: str = "",
    ) -> PolicyProposal:
        """Assign a reviewer/admin owner to a proposal."""
        proposal = self._get_or_raise(proposal_id)
        terminal = {
            ProposalStatus.DEPLOYED,
            ProposalStatus.REJECTED,
            ProposalStatus.WITHDRAWN,
        }
        if proposal.status in terminal:
            raise ValueError(
                f"Cannot assign proposal in {proposal.status.value} status"
            )
        reviewer_name = reviewer.strip()
        if not reviewer_name:
            raise ValueError("Reviewer assignment requires a reviewer username.")

        proposal.assigned_reviewer = reviewer_name
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="assigned",
                actor=actor,
                note=note or f"Assigned to {reviewer_name}.",
            )
        )
        return proposal

    def approve(
        self,
        proposal_id: str,
        *,
        approver: str = "unknown",
        note: str = "",
    ) -> PolicyProposal:
        """Approve a proposal for deployment.

        Args:
            proposal_id: ID of the proposal to approve.
            approver: Who is approving the change.

        Returns:
            The updated proposal.

        Raises:
            KeyError: If the proposal doesn't exist.
            ValueError: If prerequisites aren't met.
        """
        proposal = self._get_or_raise(proposal_id)
        self._raise_if_stale(proposal, action="approve")

        valid_statuses = {ProposalStatus.VALIDATED, ProposalStatus.SIMULATED}
        if proposal.status not in valid_statuses:
            raise ValueError(
                f"Cannot approve proposal in {proposal.status.value} status. "
                f"Must be in: {', '.join(s.value for s in valid_statuses)}"
            )

        if self.require_simulation and proposal.simulation_report is None:
            raise ValueError(
                "Simulation is required before approval. Run simulate_proposal() first."
            )

        proposal.approved_by = approver
        proposal.approved_at = datetime.now(timezone.utc)
        proposal.status = ProposalStatus.APPROVED
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="approved",
                actor=approver,
                note=note or "Approved for deployment.",
            )
        )
        logger.info("Policy proposal approved: %s by %s", proposal_id, approver)
        return proposal

    def reject(
        self,
        proposal_id: str,
        *,
        reason: str = "",
        actor: str = "unknown",
    ) -> PolicyProposal:
        """Reject a proposal.

        Args:
            proposal_id: ID of the proposal to reject.
            reason: Why the proposal was rejected.

        Returns:
            The updated proposal.
        """
        proposal = self._get_or_raise(proposal_id)
        terminal = {
            ProposalStatus.DEPLOYED,
            ProposalStatus.REJECTED,
            ProposalStatus.WITHDRAWN,
        }
        if proposal.status in terminal:
            raise ValueError(
                f"Cannot reject proposal in {proposal.status.value} status"
            )

        proposal.status = ProposalStatus.REJECTED
        proposal.rejection_reason = reason
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="rejected",
                actor=actor,
                note=reason,
            )
        )
        logger.info("Policy proposal rejected: %s — %s", proposal_id, reason)
        return proposal

    def withdraw(
        self,
        proposal_id: str,
        *,
        actor: str = "unknown",
        note: str = "",
    ) -> PolicyProposal:
        """Withdraw a proposal (by the author).

        Args:
            proposal_id: ID of the proposal to withdraw.

        Returns:
            The updated proposal.
        """
        proposal = self._get_or_raise(proposal_id)
        terminal = {ProposalStatus.DEPLOYED, ProposalStatus.WITHDRAWN}
        if proposal.status in terminal:
            raise ValueError(
                f"Cannot withdraw proposal in {proposal.status.value} status"
            )

        proposal.status = ProposalStatus.WITHDRAWN
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="withdrawn",
                actor=actor,
                note=note or "Withdrawn before deployment.",
            )
        )
        return proposal

    # ── Deploy ────────────────────────────────────────────────

    async def deploy(
        self,
        proposal_id: str,
        *,
        actor: str = "unknown",
        note: str = "",
    ) -> PolicyProposal:
        """Deploy an approved proposal to the live engine.

        Applies the proposed change (add, swap, or remove) to the
        underlying PolicyEngine.

        Args:
            proposal_id: ID of the proposal to deploy.

        Returns:
            The updated proposal with deployment record.

        Raises:
            KeyError: If the proposal doesn't exist.
            ValueError: If the proposal isn't approved.
        """
        proposal = self._get_or_raise(proposal_id)
        self._raise_if_stale(proposal, action="deploy")

        if self.require_approval and proposal.status != ProposalStatus.APPROVED:
            raise ValueError(
                f"Cannot deploy proposal in {proposal.status.value} status. "
                "Must be approved first."
            )

        # Also allow deploying validated/simulated if approval not required
        if not self.require_approval and proposal.status not in (
            ProposalStatus.VALIDATED,
            ProposalStatus.SIMULATED,
            ProposalStatus.APPROVED,
        ):
            raise ValueError(
                f"Cannot deploy proposal in {proposal.status.value} status"
            )

        if proposal.action == ProposalAction.ADD:
            assert proposal.new_provider is not None
            await self._engine.add_provider(
                proposal.new_provider,
                reason=f"Governance: {proposal.description or 'Policy proposal'}",
                author=proposal.author,
            )
            proposal.deployment_record = {"action": "add"}

        elif proposal.action == ProposalAction.SWAP:
            assert proposal.new_provider is not None
            assert proposal.target_index is not None
            record = await self._engine.hot_swap(
                proposal.target_index,
                proposal.new_provider,
                reason=f"Governance: {proposal.description} (by {proposal.author})",
            )
            proposal.deployment_record = record

        elif proposal.action == ProposalAction.REMOVE:
            assert proposal.target_index is not None
            removed = await self._engine.remove_provider(
                proposal.target_index,
                reason=f"Governance: {proposal.description or 'Policy proposal'}",
                author=proposal.author,
            )
            proposal.deployment_record = {
                "action": "remove",
                "removed_type": type(removed).__name__,
            }

        proposal.deployed_at = datetime.now(timezone.utc)
        proposal.status = ProposalStatus.DEPLOYED
        proposal.decision_trail.append(
            PolicyProposalEvent(
                event="deployed",
                actor=actor,
                note=note or "Applied to the live policy chain.",
            )
        )
        logger.info("Policy proposal deployed: %s", proposal_id)
        return proposal

    # ── Helpers ────────────────────────────────────────────────

    def _get_or_raise(self, proposal_id: str) -> PolicyProposal:
        """Get a proposal or raise KeyError."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal not found: {proposal_id}")
        return proposal

    def _current_version_number(self) -> int | None:
        """Return the active live policy version, if versioning is enabled."""
        version_manager = self._engine.version_manager
        current_version = (
            version_manager.current_version if version_manager is not None else None
        )
        return current_version.version_number if current_version is not None else None

    def _raise_if_stale(self, proposal: PolicyProposal, *, action: str) -> None:
        """Reject operations on proposals drafted against an older live version."""
        current_version = self._current_version_number()
        base_version = proposal.base_version_number
        if (
            current_version is None
            or base_version is None
            or current_version == base_version
        ):
            return

        raise ValueError(
            f"Cannot {action} proposal based on policy version {base_version} while "
            f"the live policy chain is now on version {current_version}. "
            "Create a fresh proposal from the current chain."
        )
