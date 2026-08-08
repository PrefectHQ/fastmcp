"""Stable terminal and JSON output for Horizon CLI commands."""

from __future__ import annotations

import json
import sys
from typing import Literal

from rich.console import Console

from fastmcp.cli.deploy.horizon_client import (
    DeviceAuthorization,
    HorizonOrganization,
    HorizonUser,
)

CommandName = Literal["login", "logout", "whoami"]
ErrorCategory = Literal[
    "authentication_invalid",
    "authentication_required",
    "authorization_denied",
    "authorization_expired",
    "authorization_failed",
    "horizon_error",
    "horizon_unavailable",
    "remote_revocation_failed",
    "state_error",
]

console = Console()
error_console = Console(stderr=True)


def _write_json(payload: object, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    print(json.dumps(payload, separators=(",", ":")), file=stream, flush=True)


def emit_device_challenge(
    authorization: DeviceAuthorization,
    *,
    json_output: bool,
) -> None:
    """Show a device challenge before polling starts."""
    if json_output:
        _write_json(
            {
                "event": "device_authorization",
                "verificationUrl": authorization.verification_uri,
                "verificationUrlComplete": authorization.verification_uri_complete,
                "userCode": authorization.user_code,
            },
            stderr=True,
        )
        return

    console.print("Open this URL to sign in to Prefect Horizon:")
    console.print(authorization.verification_uri)
    console.print(f"Enter code: {authorization.user_code}")
    console.print("Waiting for approval...")


def emit_identity(
    command: Literal["login", "whoami"],
    user: HorizonUser,
    organizations: tuple[HorizonOrganization, ...],
    *,
    json_output: bool,
) -> None:
    """Show the authenticated user and current organization memberships."""
    if json_output:
        _write_json(
            {
                "ok": True,
                "command": command,
                "user": user.model_dump(mode="json"),
                "organizations": [
                    organization.model_dump(mode="json")
                    for organization in organizations
                ],
            }
        )
        return

    prefix = "Signed in" if command == "login" else "Authenticated"
    display_name = f"{user.name} <{user.email}>" if user.name else user.email
    console.print(f"{prefix} as {display_name}.", markup=False)
    if not organizations:
        console.print("Organization memberships: none")
        return

    console.print("Organization memberships:")
    for organization in organizations:
        console.print(f"- {organization.name} ({organization.slug})", markup=False)


def emit_logout(
    *,
    remote_revoked: bool,
    json_output: bool,
) -> None:
    """Show a successful local logout result."""
    if json_output:
        _write_json(
            {
                "ok": True,
                "command": "logout",
                "localCredentialRemoved": True,
                "remoteRevoked": remote_revoked,
            }
        )
        return

    if remote_revoked:
        console.print("Signed out of Prefect Horizon.")
    else:
        console.print("No active Horizon credential remains on this device.")


def emit_error(
    command: CommandName,
    category: ErrorCategory,
    message: str,
    *,
    json_output: bool,
    details: dict[str, object] | None = None,
) -> None:
    """Show a stable expected command failure."""
    if json_output:
        payload: dict[str, object] = {
            "ok": False,
            "command": command,
            "error": {
                "category": category,
                "message": message,
            },
        }
        if details:
            payload.update(details)
        _write_json(payload)
        return

    error_console.print(f"Error: {message}", markup=False)
