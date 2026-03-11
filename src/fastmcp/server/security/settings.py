"""SecureMCP-owned settings.

These settings are intentionally separate from ``fastmcp.settings`` so the
extension can evolve without adding more surface area to FastMCP core.
"""

from __future__ import annotations

import inspect
import os
from typing import Annotated

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = os.getenv("SECUREMCP_ENV_FILE", os.getenv("FASTMCP_ENV_FILE", ".env"))


class SecuritySettings(BaseSettings):
    """Runtime settings for SecureMCP's extension-owned integration path.

    Canonical environment variables use the ``SECUREMCP_`` prefix. Legacy
    ``FASTMCP_SECURITY_`` aliases are still accepted during migration.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore",
        validate_assignment=True,
    )

    enabled: Annotated[
        bool,
        Field(
            validation_alias=AliasChoices(
                "SECUREMCP_ENABLED",
                "FASTMCP_SECURITY_ENABLED",
            ),
            description=inspect.cleandoc(
                """
                Master switch for SecureMCP's helper-based integration path.
                When disabled, ``attach_security`` attaches an empty context
                and registers no security middleware.
                """
            ),
        ),
    ] = True

    policy_fail_closed: Annotated[
        bool,
        Field(
            validation_alias=AliasChoices(
                "SECUREMCP_POLICY_FAIL_CLOSED",
                "FASTMCP_SECURITY_POLICY_FAIL_CLOSED",
            ),
            description=inspect.cleandoc(
                """
                Default policy fail-closed mode for environments that derive
                policy config from settings.
                """
            ),
        ),
    ] = True

    policy_bypass_stdio: Annotated[
        bool,
        Field(
            validation_alias=AliasChoices(
                "SECUREMCP_POLICY_BYPASS_STDIO",
                "FASTMCP_SECURITY_POLICY_BYPASS_STDIO",
            ),
            description=inspect.cleandoc(
                """
                Whether helper-based SecureMCP attachment bypasses STDIO
                transport by default when wiring middleware.
                """
            ),
        ),
    ] = True

    policy_hot_swap: Annotated[
        bool,
        Field(
            validation_alias=AliasChoices(
                "SECUREMCP_POLICY_HOT_SWAP",
                "FASTMCP_SECURITY_POLICY_HOT_SWAP",
            ),
            description=inspect.cleandoc(
                """
                Default policy hot-swap mode for environments that derive
                policy config from settings.
                """
            ),
        ),
    ] = True


def get_security_settings() -> SecuritySettings:
    """Load the current SecureMCP settings from env and the configured .env."""

    return SecuritySettings()


__all__ = [
    "SecuritySettings",
    "get_security_settings",
]
