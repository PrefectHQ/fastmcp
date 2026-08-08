"""Global non-secret configuration for the FastMCP CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fastmcp.cli.deploy.credentials import CredentialStore
from fastmcp.cli.deploy.horizon_client import (
    DEFAULT_HORIZON_API_ORIGIN,
    normalize_api_origin,
)
from fastmcp.cli.deploy.state import read_state, state_lock, write_state


class HorizonConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    api_origin: str = Field(alias="apiOrigin")

    @field_validator("api_origin")
    @classmethod
    def validate_api_origin(cls, value: str) -> str:
        return normalize_api_origin(value)


class ConfigurationStore:
    """Persist the Horizon API origin without organization state."""

    def __init__(self, state_directory: Path | None = None) -> None:
        if state_directory is None:
            import fastmcp

            state_directory = fastmcp.settings.home / "cli"
        self.path = state_directory / "config.json"

    def load(self) -> HorizonConfiguration:
        state = read_state(self.path, HorizonConfiguration)
        if state is not None:
            return state
        return HorizonConfiguration(
            schemaVersion=1,
            apiOrigin=DEFAULT_HORIZON_API_ORIGIN,
        )

    def save(self, configuration: HorizonConfiguration) -> None:
        write_state(
            self.path,
            configuration.model_dump(mode="json", by_alias=True),
        )

    def set_api_origin(
        self,
        api_origin: str,
        *,
        credentials: CredentialStore,
    ) -> HorizonConfiguration:
        """Set the origin and clear credentials before an origin change."""
        with state_lock(self.path.parent):
            current = self.load()
            updated = HorizonConfiguration(schemaVersion=1, apiOrigin=api_origin)
            if updated.api_origin != current.api_origin:
                credentials.clear()
            self.save(updated)
            return updated
