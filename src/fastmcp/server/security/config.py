"""Security configuration for SecureMCP.

SecurityConfig is the master configuration object that wires together
all security layers. Pass it to FastMCP() to enable security features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.contracts.crypto import ContractCryptoHandler
from fastmcp.server.security.contracts.exchange_log import ExchangeLog
from fastmcp.server.security.contracts.schema import ContractTerm
from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.policy.invariants import InvariantRegistry
from fastmcp.server.security.policy.provider import PolicyProvider
from fastmcp.server.security.provenance.ledger import ProvenanceLedger

if TYPE_CHECKING:
    pass


@dataclass
class PolicyConfig:
    """Configuration for the Policy Kernel layer.

    Attributes:
        engine: Pre-built PolicyEngine instance. If None, one is created
            from the providers list.
        providers: Policy providers to use (ignored if engine is set).
        fail_closed: Deny on evaluation failure/error.
        allow_hot_swap: Permit runtime policy replacement.
        invariant_registry: Registry for formal verification invariants.
    """

    engine: PolicyEngine | None = None
    providers: list[PolicyProvider] | None = None
    fail_closed: bool = True
    allow_hot_swap: bool = True
    invariant_registry: InvariantRegistry | None = None

    def get_engine(self) -> PolicyEngine:
        """Get or create the policy engine."""
        if self.engine is not None:
            return self.engine
        return PolicyEngine(
            providers=self.providers,
            fail_closed=self.fail_closed,
            allow_hot_swap=self.allow_hot_swap,
        )


@dataclass
class ContractConfig:
    """Configuration for the Context Broker layer (Phase 2).

    Attributes:
        broker: Pre-built ContextBroker instance. If None, one is created
            from the other settings.
        crypto_handler: Handler for contract signing/verification.
        exchange_log: Log for non-repudiation audit trail.
        default_terms: Server-mandated terms added to every contract.
        term_evaluator: Async callable to evaluate proposed terms.
        max_rounds: Maximum negotiation rounds.
        session_timeout: How long negotiation sessions stay active.
        contract_duration: Default contract validity duration.
        require_for_list: Require contract for list operations.
    """

    broker: ContextBroker | None = None
    crypto_handler: ContractCryptoHandler | None = None
    exchange_log: ExchangeLog | None = None
    default_terms: list[ContractTerm] | None = None
    term_evaluator: Any = None
    max_rounds: int = 5
    session_timeout: timedelta = field(default_factory=lambda: timedelta(minutes=30))
    contract_duration: timedelta = field(default_factory=lambda: timedelta(hours=1))
    require_for_list: bool = False

    def get_broker(self, server_id: str = "securemcp-server") -> ContextBroker:
        """Get or create the context broker."""
        if self.broker is not None:
            return self.broker
        return ContextBroker(
            server_id=server_id,
            crypto_handler=self.crypto_handler,
            exchange_log=self.exchange_log,
            term_evaluator=self.term_evaluator,
            default_terms=self.default_terms,
            max_rounds=self.max_rounds,
            session_timeout=self.session_timeout,
            contract_duration=self.contract_duration,
        )


@dataclass
class ProvenanceConfig:
    """Configuration for the Provenance Ledger layer (Phase 3).

    Attributes:
        ledger: Pre-built ProvenanceLedger instance. If None, one is created.
        ledger_id: Identifier for the ledger instance.
        record_list_operations: If True, record list operations in addition
            to execution operations.
    """

    ledger: ProvenanceLedger | None = None
    ledger_id: str = "default"
    record_list_operations: bool = False

    def get_ledger(self) -> ProvenanceLedger:
        """Get or create the provenance ledger."""
        if self.ledger is not None:
            return self.ledger
        return ProvenanceLedger(ledger_id=self.ledger_id)


@dataclass
class SecurityConfig:
    """Master security configuration for SecureMCP.

    Pass to ``FastMCP(security_config=...)`` to enable security layers.

    Example::

        from fastmcp.server.security import SecurityConfig
        from fastmcp.server.security.policy import GDPRPolicy, HIPAAPolicy

        config = SecurityConfig(
            policy=PolicyConfig(
                providers=[GDPRPolicy(), HIPAAPolicy()],
                fail_closed=True,
            ),
        )
        mcp = FastMCP("my-server", security_config=config)

    Attributes:
        policy: Policy Kernel configuration (Phase 1).
        contracts: Context Broker configuration (Phase 2).
        provenance: Provenance Ledger configuration (Phase 3).
        enabled: Master switch to enable/disable all security layers.
    """

    policy: PolicyConfig | None = None
    contracts: ContractConfig | None = None
    provenance: ProvenanceConfig | None = None
    enabled: bool = True

    def is_policy_enabled(self) -> bool:
        """Check if the policy layer is configured and active."""
        return self.enabled and self.policy is not None

    def is_contracts_enabled(self) -> bool:
        """Check if the contracts layer is configured and active."""
        return self.enabled and self.contracts is not None

    def is_provenance_enabled(self) -> bool:
        """Check if the provenance layer is configured and active."""
        return self.enabled and self.provenance is not None
