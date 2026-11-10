"""Restricted Horizon credential storage and resolution."""

from __future__ import annotations

import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from fastmcp.cli.deploy.horizon_client import HorizonClient, normalize_api_origin
from fastmcp.cli.deploy.state import (
    StateFileError,
    read_state,
    remove_state,
    state_lock,
    write_state,
)

CredentialSource = Literal["environment", "stored", "interactive"]


class AuthenticationRequiredError(RuntimeError):
    """No Horizon credential is available without interactive authorization."""


class AuthState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    api_key: SecretStr = Field(alias="apiKey")

    @field_validator("api_key")
    @classmethod
    def require_nonempty_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("The API key is empty")
        return value


@dataclass(frozen=True)
class ResolvedCredential:
    api_key: SecretStr
    source: CredentialSource


class CredentialStore:
    """Persist the active personal Horizon API key."""

    def __init__(self, state_directory: Path | None = None) -> None:
        if state_directory is None:
            import fastmcp

            state_directory = fastmcp.settings.home / "cli"
        self.path = state_directory / "auth.json"

    def load(self) -> SecretStr | None:
        state = read_state(self.path, AuthState, secret=True)
        return state.api_key if state is not None else None

    def save(self, api_key: SecretStr | str) -> None:
        try:
            state = AuthState(schemaVersion=1, apiKey=api_key)
        except ValidationError:
            raise StateFileError("The Horizon API key is invalid") from None
        write_state(
            self.path,
            {
                "schemaVersion": state.schema_version,
                "apiKey": state.api_key.get_secret_value(),
            },
        )

    def save_for_origin(
        self,
        api_key: SecretStr | str,
        *,
        expected_api_origin: str,
    ) -> None:
        """Save a key only while its issuing Horizon origin is active."""
        from fastmcp.cli.deploy.configuration import ConfigurationStore

        expected_api_origin = normalize_api_origin(expected_api_origin)
        with state_lock(self.path.parent):
            active_api_origin = ConfigurationStore(self.path.parent).load().api_origin
            if active_api_origin != expected_api_origin:
                raise StateFileError("The Horizon host changed during login")
            self.save(api_key)

    def clear_if_matches(
        self,
        api_key: SecretStr | str,
        *,
        expected_api_origin: str,
    ) -> None:
        """Clear a key only while its Horizon origin and value are active."""
        from fastmcp.cli.deploy.configuration import ConfigurationStore

        expected_api_origin = normalize_api_origin(expected_api_origin)
        expected_api_key = (
            api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        )
        with state_lock(self.path.parent):
            active_api_origin = ConfigurationStore(self.path.parent).load().api_origin
            active_api_key = self.load()
            if (
                active_api_origin == expected_api_origin
                and active_api_key is not None
                and secrets.compare_digest(
                    active_api_key.get_secret_value(),
                    expected_api_key,
                )
            ):
                self.clear()

    def clear(self) -> None:
        remove_state(self.path)


async def resolve_credential(
    store: CredentialStore,
    *,
    environ: Mapping[str, str] | None = None,
    authorize: Callable[[], Awaitable[SecretStr]] | None = None,
    expected_api_origin: str | None = None,
) -> ResolvedCredential:
    """Resolve environment, stored, then interactive credentials."""
    environ = os.environ if environ is None else environ
    environment_key = environ.get("HORIZON_API_KEY")
    if environment_key:
        return ResolvedCredential(
            api_key=SecretStr(environment_key),
            source="environment",
        )

    stored_key = store.load()
    if stored_key is not None:
        return ResolvedCredential(api_key=stored_key, source="stored")

    if authorize is None:
        raise AuthenticationRequiredError("Horizon authentication is required")

    api_key = await authorize()
    if expected_api_origin is None:
        store.save(api_key)
    else:
        store.save_for_origin(api_key, expected_api_origin=expected_api_origin)
    return ResolvedCredential(api_key=api_key, source="interactive")


async def revoke_and_clear_credential(
    client: HorizonClient,
    store: CredentialStore,
) -> None:
    """Attempt remote revocation and always remove the stored credential."""
    try:
        await client.revoke_current_api_key()
    finally:
        store.clear()
