"""Security configuration for SecureMCP.

SecurityConfig is the master configuration object that wires together
all security layers. Pass it to FastMCP() to enable security features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.certification.attestation import CertificationLevel
from fastmcp.server.security.certification.pipeline import CertificationPipeline
from fastmcp.server.security.certification.validator import ManifestValidator
from fastmcp.server.security.compliance.reports import (
    ComplianceFramework,
    ComplianceReporter,
)
from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.contracts.crypto import ContractCryptoHandler
from fastmcp.server.security.contracts.exchange_log import ExchangeLog
from fastmcp.server.security.contracts.schema import ContractTerm
from fastmcp.server.security.federation.crl import CertificateRevocationList
from fastmcp.server.security.federation.federation import TrustFederation
from fastmcp.server.security.gateway.audit import AuditAPI
from fastmcp.server.security.gateway.marketplace import Marketplace
from fastmcp.server.security.gateway.tool_marketplace import ToolMarketplace
from fastmcp.server.security.policy.audit import PolicyAuditLog
from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.policy.governance import PolicyGovernor
from fastmcp.server.security.policy.invariants import InvariantRegistry
from fastmcp.server.security.policy.monitoring import PolicyMonitor
from fastmcp.server.security.policy.provider import PolicyProvider
from fastmcp.server.security.policy.validator import PolicyValidator
from fastmcp.server.security.policy.versioning.manager import PolicyVersionManager
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.reflexive.analyzer import (
    BehavioralAnalyzer,
    EscalationEngine,
)
from fastmcp.server.security.reflexive.models import DriftSeverity, EscalationRule
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.sandbox.enforcer import SandboxedRunner
from fastmcp.server.security.storage.backend import StorageBackend

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
        audit_log: Pre-built PolicyAuditLog. If None, one is auto-created.
        audit_max_entries: Max entries for the auto-created audit log.
        policy_file: Path or dict for declarative policy loading. When set,
            the loaded policy is prepended to the providers list.
        enable_versioning: If True, a PolicyVersionManager is created and
            wired to the engine's hot-swap mechanism.
    """

    engine: PolicyEngine | None = None
    providers: list[PolicyProvider] | None = None
    fail_closed: bool = True
    allow_hot_swap: bool = True
    invariant_registry: InvariantRegistry | None = None
    audit_log: PolicyAuditLog | None = None
    audit_max_entries: int = 10_000
    policy_file: str | dict | None = None
    enable_versioning: bool = False
    enable_monitoring: bool = False
    enable_governance: bool = False
    enable_validation: bool = True
    validator: PolicyValidator | None = None
    monitor: PolicyMonitor | None = None
    backend: StorageBackend | None = None

    def get_audit_log(self) -> PolicyAuditLog:
        """Get or create the policy audit log."""
        if self.audit_log is not None:
            return self.audit_log
        return PolicyAuditLog(max_entries=self.audit_max_entries)

    def get_version_manager(
        self,
        policy_set_id: str = "default",
    ) -> PolicyVersionManager | None:
        """Create a PolicyVersionManager if versioning is enabled.

        Args:
            policy_set_id: Identifier for the policy set being managed.

        Returns:
            A PolicyVersionManager if ``enable_versioning`` is True and
            a storage backend is available, else None.
        """
        if not self.enable_versioning:
            return None
        if self.backend is None:
            from fastmcp.server.security.storage.memory import MemoryBackend

            backend = MemoryBackend()
        else:
            backend = self.backend
        return PolicyVersionManager(
            policy_set_id=policy_set_id,
            backend=backend,
        )

    def get_validator(self) -> PolicyValidator | None:
        """Get or create the policy validator."""
        if not self.enable_validation:
            return None
        if self.validator is not None:
            return self.validator
        return PolicyValidator()

    def get_monitor(
        self,
        *,
        audit_log: PolicyAuditLog | None = None,
        event_bus: Any = None,
    ) -> PolicyMonitor | None:
        """Get or create the policy monitor."""
        if not self.enable_monitoring:
            return None
        if self.monitor is not None:
            return self.monitor
        return PolicyMonitor(audit_log=audit_log, event_bus=event_bus)

    def get_governor(
        self,
        engine: PolicyEngine,
        *,
        validator: PolicyValidator | None = None,
    ) -> PolicyGovernor | None:
        """Create a PolicyGovernor if governance is enabled."""
        if not self.enable_governance:
            return None
        return PolicyGovernor(
            engine=engine,
            validator=validator,
        )

    def get_engine(
        self,
        *,
        audit_log: PolicyAuditLog | None = None,
        version_manager: PolicyVersionManager | None = None,
    ) -> PolicyEngine:
        """Get or create the policy engine.

        Args:
            audit_log: Optional audit log to attach to the engine.
            version_manager: Optional version manager for hot-swap tracking.
        """
        if self.engine is not None:
            if audit_log is not None and self.engine._audit_log is None:
                self.engine._audit_log = audit_log
            if version_manager is not None and self.engine._version_manager is None:
                self.engine._version_manager = version_manager
            return self.engine

        providers = list(self.providers) if self.providers else []

        # Load declarative policy file if configured
        if self.policy_file is not None:
            from fastmcp.server.security.policy.declarative import load_policy

            declarative_provider = load_policy(self.policy_file)
            providers.insert(0, declarative_provider)

        return PolicyEngine(
            providers=providers or None,
            fail_closed=self.fail_closed,
            allow_hot_swap=self.allow_hot_swap,
            audit_log=audit_log,
            version_manager=version_manager,
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
    backend: StorageBackend | None = None

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
            backend=self.backend,
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
    backend: StorageBackend | None = None

    def get_ledger(self) -> ProvenanceLedger:
        """Get or create the provenance ledger."""
        if self.ledger is not None:
            return self.ledger
        return ProvenanceLedger(ledger_id=self.ledger_id, backend=self.backend)


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
    backend: StorageBackend | None = None

    def get_analyzer(self) -> BehavioralAnalyzer:
        """Get or create the behavioral analyzer."""
        if self.analyzer is not None:
            return self.analyzer
        return BehavioralAnalyzer(
            sigma_thresholds=self.sigma_thresholds,
            min_samples=self.min_samples,
            backend=self.backend,
        )

    def get_escalation_engine(self) -> EscalationEngine:
        """Get or create the escalation engine."""
        if self.escalation_engine is not None:
            return self.escalation_engine
        return EscalationEngine(
            rules=self.escalation_rules,
            on_escalation=self.on_escalation,
            backend=self.backend,
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
    backend: StorageBackend | None = None

    def get_graph(self) -> ConsentGraph:
        """Get or create the consent graph."""
        if self.graph is not None:
            return self.graph
        return ConsentGraph(graph_id=self.graph_id, backend=self.backend)


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
    backend: StorageBackend | None = None

    def get_marketplace(self) -> Marketplace:
        """Get or create the marketplace."""
        if self.marketplace is not None:
            return self.marketplace
        return Marketplace(marketplace_id=self.marketplace_id, backend=self.backend)


@dataclass
class AlertConfig:
    """Configuration for the Real-time Alert System (Phase 9).

    Attributes:
        event_bus: Pre-built SecurityEventBus instance. If None, one is created.
        propagate_to_components: If True, the event bus is automatically
            injected into all configured components.
    """

    event_bus: SecurityEventBus | None = None
    propagate_to_components: bool = True

    def get_event_bus(self) -> SecurityEventBus:
        """Get or create the event bus."""
        if self.event_bus is not None:
            return self.event_bus
        return SecurityEventBus()


@dataclass
class CertificationConfig:
    """Configuration for the Tool Certification layer (Phase 12).

    Attributes:
        pipeline: Pre-built CertificationPipeline instance. If None, one is created.
        issuer_id: Identity of the certification authority.
        crypto_handler: Handler for signing attestations. Reuses contracts crypto if None.
        validator: Pre-built ManifestValidator. If None, defaults are used.
        min_level_for_signing: Minimum certification level to produce signed attestations.
    """

    pipeline: CertificationPipeline | None = None
    issuer_id: str = "securemcp-ca"
    crypto_handler: ContractCryptoHandler | None = None
    validator: ManifestValidator | None = None
    min_level_for_signing: CertificationLevel = CertificationLevel.BASIC

    def get_pipeline(
        self,
        *,
        marketplace: Any = None,
        event_bus: Any = None,
        fallback_crypto: ContractCryptoHandler | None = None,
    ) -> CertificationPipeline:
        """Get or create the certification pipeline."""
        if self.pipeline is not None:
            return self.pipeline
        crypto = self.crypto_handler or fallback_crypto
        return CertificationPipeline(
            issuer_id=self.issuer_id,
            crypto_handler=crypto,
            validator=self.validator,
            marketplace=marketplace,
            event_bus=event_bus,
            min_level_for_signing=self.min_level_for_signing,
        )


@dataclass
class RegistryConfig:
    """Configuration for the Trust Registry.

    Attributes:
        registry: Pre-built TrustRegistry. If None, one is created.
    """

    registry: TrustRegistry | None = None

    def get_registry(
        self, *, event_bus: SecurityEventBus | None = None
    ) -> TrustRegistry:
        """Get or create the trust registry."""
        if self.registry is not None:
            return self.registry
        return TrustRegistry(event_bus=event_bus)


@dataclass
class ToolMarketplaceConfig:
    """Configuration for the Tool Marketplace.

    Attributes:
        marketplace: Pre-built ToolMarketplace. If None, one is created.
    """

    marketplace: ToolMarketplace | None = None

    def get_marketplace(
        self,
        *,
        trust_registry: TrustRegistry | None = None,
        event_bus: SecurityEventBus | None = None,
    ) -> ToolMarketplace:
        """Get or create the tool marketplace."""
        if self.marketplace is not None:
            return self.marketplace
        return ToolMarketplace(trust_registry=trust_registry, event_bus=event_bus)


@dataclass
class FederationConfig:
    """Configuration for the Trust Federation.

    Attributes:
        federation: Pre-built TrustFederation. If None, one is created.
        federation_id: Identifier for this federation node.
    """

    federation: TrustFederation | None = None
    federation_id: str = "default"

    def get_federation(
        self,
        *,
        local_registry: TrustRegistry | None = None,
        local_crl: CertificateRevocationList | None = None,
        event_bus: SecurityEventBus | None = None,
    ) -> TrustFederation:
        """Get or create the trust federation."""
        if self.federation is not None:
            return self.federation
        return TrustFederation(
            federation_id=self.federation_id,
            local_registry=local_registry,
            local_crl=local_crl,
            event_bus=event_bus,
        )


@dataclass
class CRLConfig:
    """Configuration for the Certificate Revocation List.

    Attributes:
        crl: Pre-built CertificateRevocationList. If None, one is created.
    """

    crl: CertificateRevocationList | None = None

    def get_crl(self) -> CertificateRevocationList:
        """Get or create the CRL."""
        if self.crl is not None:
            return self.crl
        return CertificateRevocationList()


@dataclass
class ComplianceConfig:
    """Configuration for the Compliance Reporter.

    Attributes:
        reporter: Pre-built ComplianceReporter. If None, one is created.
        framework: Compliance framework to use. If None, SecureMCP default.
    """

    reporter: ComplianceReporter | None = None
    framework: ComplianceFramework | None = None

    def get_reporter(self) -> ComplianceReporter:
        """Get or create the compliance reporter."""
        if self.reporter is not None:
            return self.reporter
        return ComplianceReporter(framework=self.framework)


@dataclass
class SandboxConfig:
    """Configuration for the Sandboxed Execution Runner.

    Attributes:
        runner: Pre-built SandboxedRunner. If None, one is created.
    """

    runner: SandboxedRunner | None = None

    def get_runner(
        self,
        *,
        crl: CertificateRevocationList | None = None,
    ) -> SandboxedRunner:
        """Get or create the sandboxed runner."""
        if self.runner is not None:
            return self.runner
        return SandboxedRunner(crl=crl)


@dataclass
class SecurityConfig:
    """Master security configuration for SecureMCP.

    Use with ``attach_security(server, config)`` to enable security layers.

    Example::

        from fastmcp import FastMCP
        from fastmcp.server.security import SecurityConfig, attach_security
        from fastmcp.server.security.policy import GDPRPolicy, HIPAAPolicy, PolicyConfig

        config = SecurityConfig(
            policy=PolicyConfig(
                providers=[GDPRPolicy(), HIPAAPolicy()],
                fail_closed=True,
            ),
        )
        mcp = FastMCP("my-server")
        attach_security(mcp, config)

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
    alerts: AlertConfig | None = None
    certification: CertificationConfig | None = None
    registry: RegistryConfig | None = None
    tool_marketplace: ToolMarketplaceConfig | None = None
    federation: FederationConfig | None = None
    crl_config: CRLConfig | None = None
    compliance: ComplianceConfig | None = None
    sandbox: SandboxConfig | None = None
    enabled: bool = True
    backend: StorageBackend | None = None

    def __post_init__(self) -> None:
        """Propagate shared backend to layer configs that don't have one."""
        if self.backend is not None:
            for layer in [
                self.policy,
                self.contracts,
                self.provenance,
                self.reflexive,
                self.consent,
                self.gateway,
            ]:
                if layer is not None and getattr(layer, "backend", None) is None:
                    layer.backend = self.backend

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

    def is_alerts_enabled(self) -> bool:
        """Check if the alerts layer is configured and active."""
        return self.enabled and self.alerts is not None

    def is_certification_enabled(self) -> bool:
        """Check if the certification layer is configured and active."""
        return self.enabled and self.certification is not None

    def is_registry_enabled(self) -> bool:
        """Check if the trust registry is configured and active."""
        return self.enabled and self.registry is not None

    def is_tool_marketplace_enabled(self) -> bool:
        """Check if the tool marketplace is configured and active."""
        return self.enabled and self.tool_marketplace is not None

    def is_federation_enabled(self) -> bool:
        """Check if the trust federation is configured and active."""
        return self.enabled and self.federation is not None

    def is_crl_enabled(self) -> bool:
        """Check if the CRL is configured and active."""
        return self.enabled and self.crl_config is not None

    def is_compliance_enabled(self) -> bool:
        """Check if the compliance reporter is configured and active."""
        return self.enabled and self.compliance is not None

    def is_sandbox_enabled(self) -> bool:
        """Check if the sandbox runner is configured and active."""
        return self.enabled and self.sandbox is not None
