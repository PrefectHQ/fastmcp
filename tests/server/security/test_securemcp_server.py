from __future__ import annotations

import pytest

import securemcp
from securemcp import SecureMCP
from securemcp.config import GatewayConfig, RegistryConfig, SecurityConfig
from securemcp.http import SecurityAPI
from securemcp.settings import SecuritySettings


class TestSecureMCPFacade:
    def test_root_exports(self):
        assert securemcp.SecureMCP is SecureMCP
        assert securemcp.SecurityConfig is SecurityConfig
        assert securemcp.SecurityAPI is SecurityAPI
        assert securemcp.SecuritySettings is SecuritySettings

    def test_constructor_attaches_security(self):
        server = SecureMCP(
            "secure-server",
            security=SecurityConfig(registry=RegistryConfig()),
        )

        assert server.security_context is not None
        assert server.security_context.registry is not None

    def test_attach_security_method_sets_context(self):
        server = SecureMCP("secure-server")

        ctx = server.attach_security(
            SecurityConfig(registry=RegistryConfig()),
        )

        assert server.security_context is ctx

    def test_constructor_mounts_security_api(self):
        server = SecureMCP(
            "secure-server",
            security=SecurityConfig(registry=RegistryConfig()),
            mount_security_api=True,
        )

        assert server.security_api is not None

        paths = {route.path for route in server._additional_http_routes}
        assert "/security/dashboard" in paths
        assert "/security/health" in paths

    @pytest.mark.asyncio
    async def test_constructor_can_register_gateway_tools(self):
        server = SecureMCP(
            "secure-server",
            security=SecurityConfig(gateway=GatewayConfig()),
            register_gateway_tools=True,
        )

        tools = await server.list_tools(run_middleware=False)
        tool_names = {tool.name for tool in tools}

        assert "securemcp_security_status" in tool_names
        assert "securemcp_marketplace_stats" in tool_names
