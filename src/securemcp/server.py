"""SecureMCP server facade."""

from __future__ import annotations

from typing import Any, Generic

from mcp.server.lowlevel.server import LifespanResultT

from fastmcp import FastMCP
from fastmcp.server.security.orchestrator import SecurityContext
from securemcp.config import SecurityConfig
from securemcp.http import SecurityAPI, mount_security_routes
from securemcp.integration import (
    attach_security,
    attach_security_context,
    get_security_context,
    register_security_gateway_tools,
)
from securemcp.settings import SecuritySettings


class SecureMCP(FastMCP[LifespanResultT], Generic[LifespanResultT]):
    """FastMCP wrapper that makes SecureMCP a first-class server type.

    This keeps the security integration path additive: FastMCP remains
    upstream-owned, while SecureMCP layers are attached through public hooks.

    Example:
        ```python
        from securemcp import SecureMCP
        from securemcp.config import RegistryConfig, SecurityConfig

        server = SecureMCP(
            "my-secure-server",
            security=SecurityConfig(registry=RegistryConfig()),
            mount_security_api=True,
        )
        ```
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        security: SecurityConfig | None = None,
        mount_security_api: bool = False,
        security_api: SecurityAPI | None = None,
        security_api_prefix: str = "/security",
        bypass_stdio: bool | None = None,
        security_settings: SecuritySettings | None = None,
        register_gateway_tools: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)

        self._securemcp_api: SecurityAPI | None = None

        if security is not None:
            self.attach_security(
                security,
                bypass_stdio=bypass_stdio,
                settings=security_settings,
                register_gateway_tools=register_gateway_tools,
            )
        elif register_gateway_tools:
            raise ValueError(
                "`register_gateway_tools=True` requires `security=` or an attached security context."
            )

        if mount_security_api or security_api is not None:
            self.mount_security_api(
                api=security_api,
                prefix=security_api_prefix,
            )

    @property
    def security_context(self) -> SecurityContext | None:
        """Return the attached SecureMCP context, if present."""

        return get_security_context(self)

    @property
    def security_api(self) -> SecurityAPI | None:
        """Return the mounted SecureMCP HTTP API, if mounted via this facade."""

        return self._securemcp_api

    def attach_security(
        self,
        config: SecurityConfig,
        *,
        bypass_stdio: bool | None = None,
        settings: SecuritySettings | None = None,
        register_gateway_tools: bool = False,
    ) -> SecurityContext:
        """Bootstrap and attach SecureMCP to this server."""

        return attach_security(
            self,
            config,
            bypass_stdio=bypass_stdio,
            settings=settings,
            register_gateway_tools=register_gateway_tools,
        )

    def attach_security_context(
        self,
        context: SecurityContext,
        *,
        register_gateway_tools: bool = False,
    ) -> SecurityContext:
        """Attach an already-bootstrapped SecureMCP context."""

        return attach_security_context(
            self,
            context,
            register_gateway_tools=register_gateway_tools,
        )

    def register_gateway_tools(self) -> list[str]:
        """Register SecureMCP gateway tools on this server."""

        return register_security_gateway_tools(self)

    def mount_security_api(
        self,
        *,
        api: SecurityAPI | None = None,
        prefix: str = "/security",
    ) -> SecurityAPI:
        """Mount SecureMCP HTTP routes on this server."""

        mounted_api = mount_security_routes(self, api=api, prefix=prefix)
        self._securemcp_api = mounted_api
        return mounted_api
