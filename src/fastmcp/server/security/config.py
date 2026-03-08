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
from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.gateway.audit import AuditAPI
from fastmcp.server.security.gateway.marketplace import Marketplace
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.reflexive.analyzer import BehavioralAnalyzer, EscalationEngine
from fastmcp.server.security.reflexive.models import DriftSeverity, EscalationRule

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
class ReflexiveConfig:
    """Configuration for the Reflexive Core layer (Phase 4).

    Attributes:
        analyzer: Pre-built BehavioralAnalyzer instance. If None, one is created.
        escalation_engine: Pre-built EscalationEngine. If None, one is created
            from the escalation_rules list.
        escalation_rules: Rules for the escalation engine (ignored if
            escalation_engine is set).
        sigma_thresholds: Custom sigma thresholds for drift severity classification.
        min_samples: Minimum observations before drift detection activates.
        on_escalation: Optional async callback invoked on each escalation.
    """

    analyzer: BehavioralAnalyzer | None = None
    escalation_engine: EscalationEngine | None = None
    escalation_rules: list[EscalationRule] | None = None
    sigma_thresholds: dict[DriftSeverity, float] | None = None
    min_samples: int = 10
    on_escalation: Any = None

    def get_analyzer(self) -> BehavioralAnalyzer:
        """Get or create the behavioral analyzer."""
        if self.analyzer is not None:
            return self.analyzer
        return BehavioralAnalyzer(
            sigma_thresholds=self.sigma_thresholds,
            min_samples=self.min_samples,
        )

    def get_escalation_engine(self) -> EscalationEngine:
        """Get or create the escalation engine."""
        if self.escalation_engine is not None:
            return self.escalation_engine
        return EscalationEngine(
            rules=self.escalation_rules,
            on_escalation=self.on_escalation,
        )


@dataclass
class ConsentConfig:
    """Configuration for the Consent Graph layer (Phase 5).

    Attributes:
        graph: Pre-built ConsentGraph instance. If None, one is created.
        graph_id: Identifier for the graph instance.
        resource_owner: Default owner ID for resource consent checks.
        require_for_list: If True, check consent for list operations.
    """

    graph: ConsentGraph | None = None
    graph_id: str = "default"
    resource_owner: str = "server"
    require_for_list: bool = False

    def get_graph(self) -> ConsentGraph:
        """Get or create the consent graph."""
        if self.graph is not None:
            return self.graph
        return ConsentGraph(graph_id=self.graph_id)


@dataclass
class GatewayConfig:
    """Configuration for the API Gateway layer (Phase 6).

    Attributes:
        audit_api: Pre-built AuditAPI instance. If None, one is created
            automatically from other configured layers.
        marketplace: Pre-built Marketplace instance. If None, one is created.
        marketplace_id: Identifier for the marketplace instance.
        register_tools: If True, register audit/marketplace as MCP tools.
    """

    audit_api: AuditAPI | None = None
    marketplace: Marketplace | None = None
    marketplace_id: str = "default"
    register_tools: bool = True

    def get_marketplace(self) -> Marketplace:
        """Get or create the marketplace."""
        if self.marketplace is not None:
            return self.marketplace
        return Marketplace(marketplace_id=self.marketplace_id)


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
        reflexive: Reflexive Core configuration (Phase 4).
        consent: Consent Graph configuration (Phase 5).
        gateway: API Gateway configuration (Phase 6).
        enabled: Master switch to enable/disable all security layers.
    """

    policy: PolicyConfig | None = None
    contracts: ContractConfig | None = None
    provenance: ProvenanceConfig | None = None
    reflexive: ReflexiveConfig | None = None
    consent: ConsentConfig | None = None
    gateway: GatewayConfig | None = None
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

    def is_reflexive_enabled(self) -> bool:
        """Check if the reflexive layer is configured and active."""
        return self.enabled and self.reflexive is not None

    def is_consent_enabled(self) -> bool:
        """Check if the consent layer is configured and active."""
        return self.enabled and self.consent is not None

    def is_gateway_enabled(self) -> bool:
        """Check if the gateway layer is configured and active."""
        return self.enabled and self.gateway is not None
