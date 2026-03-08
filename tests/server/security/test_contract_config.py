"""Tests for ContractConfig and server integration."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.security.config import ContractConfig, PolicyConfig, SecurityConfig
from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.middleware.contract_validation import (
    ContractValidationMiddleware,
)
from fastmcp.server.security.middleware.policy_enforcement import (
    PolicyEnforcementMiddleware,
)
from fastmcp.server.security.policy.provider import AllowAllPolicy


class TestContractConfig:
    def test_default_config(self):
        config = ContractConfig()
        assert config.broker is None
        assert config.crypto_handler is None
        assert config.max_rounds == 5

    def test_get_broker_creates_new(self):
        config = ContractConfig()
        broker = config.get_broker(server_id="test")
        assert isinstance(broker, ContextBroker)
        assert broker.server_id == "test"

    def test_get_broker_uses_existing(self):
        existing = ContextBroker(server_id="existing")
        config = ContractConfig(broker=existing)
        assert config.get_broker() is existing


class TestSecurityConfigContracts:
    def test_contracts_disabled_by_default(self):
        config = SecurityConfig()
        assert not config.is_contracts_enabled()

    def test_contracts_enabled_when_configured(self):
        config = SecurityConfig(contracts=ContractConfig())
        assert config.is_contracts_enabled()

    def test_contracts_disabled_when_master_off(self):
        config = SecurityConfig(
            contracts=ContractConfig(),
            enabled=False,
        )
        assert not config.is_contracts_enabled()


class TestServerContractIntegration:
    def test_server_with_contracts(self):
        config = SecurityConfig(contracts=ContractConfig())
        mcp = FastMCP("test", security_config=config)
        assert any(
            isinstance(m, ContractValidationMiddleware) for m in mcp.middleware
        )

    def test_server_without_contracts(self):
        mcp = FastMCP("test")
        assert not any(
            isinstance(m, ContractValidationMiddleware) for m in mcp.middleware
        )

    def test_server_with_both_policy_and_contracts(self):
        config = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            contracts=ContractConfig(),
        )
        mcp = FastMCP("test", security_config=config)
        assert any(
            isinstance(m, PolicyEnforcementMiddleware) for m in mcp.middleware
        )
        assert any(
            isinstance(m, ContractValidationMiddleware) for m in mcp.middleware
        )

    def test_server_contracts_disabled_no_middleware(self):
        config = SecurityConfig(
            contracts=ContractConfig(),
            enabled=False,
        )
        mcp = FastMCP("test", security_config=config)
        assert not any(
            isinstance(m, ContractValidationMiddleware) for m in mcp.middleware
        )
