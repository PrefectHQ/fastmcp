"""Tests for API Gateway configuration."""

from __future__ import annotations

from fastmcp.server.security.config import (
    GatewayConfig,
    SecurityConfig,
)
from fastmcp.server.security.gateway.marketplace import Marketplace


class TestGatewayConfig:
    def test_default_config(self):
        config = GatewayConfig()
        assert config.audit_api is None
        assert config.marketplace is None
        assert config.register_tools is True

    def test_get_marketplace_default(self):
        config = GatewayConfig()
        mp = config.get_marketplace()
        assert isinstance(mp, Marketplace)
        assert mp.marketplace_id == "default"

    def test_get_marketplace_custom_id(self):
        config = GatewayConfig(marketplace_id="my-mp")
        mp = config.get_marketplace()
        assert mp.marketplace_id == "my-mp"

    def test_get_marketplace_uses_existing(self):
        custom = Marketplace(marketplace_id="custom")
        config = GatewayConfig(marketplace=custom)
        assert config.get_marketplace() is custom


class TestSecurityConfigGateway:
    def test_gateway_not_enabled_by_default(self):
        config = SecurityConfig()
        assert not config.is_gateway_enabled()

    def test_gateway_enabled(self):
        config = SecurityConfig(gateway=GatewayConfig())
        assert config.is_gateway_enabled()

    def test_gateway_disabled_by_master_switch(self):
        config = SecurityConfig(
            gateway=GatewayConfig(),
            enabled=False,
        )
        assert not config.is_gateway_enabled()
