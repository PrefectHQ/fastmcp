"""Stable terminal and JSON output for Horizon CLI commands."""

from __future__ import annotations

import json
import sys
from typing import Literal

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from fastmcp.cli.deploy.horizon_client import DeviceAuthorization, HorizonUser

CommandName = Literal["login", "logout", "whoami"]
ErrorCategory = Literal[
    "authentication_invalid",
    "authentication_required",
    "authorization_denied",
    "authorization_expired",
    "authorization_failed",
    "horizon_error",
    "horizon_unavailable",
    "invalid_host",
    "remote_revocation_failed",
    "state_error",
]

console = Console()
error_console = Console(stderr=True)


def _write_json(payload: object, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    print(json.dumps(payload, separators=(",", ":")), file=stream, flush=True)


def _banner(title: str, *, style: str) -> Panel:
    return Panel(
        Align.center(Text(title, style=f"bold {style}")),
        box=box.ROUNDED,
        border_style=style,
        padding=(0, 1),
        width=52,
    )


def _account_panel(
    user: HorizonUser,
    *,
    title: str,
    message: str,
) -> Panel:
    name = Text(user.name or user.email, style="bold")
    details: list[Text] = [name]
    if user.name:
        details.append(Text(user.email, style="cyan"))
    details.extend([Text(), Text(message, style="green")])
    return Panel(
        Group(*details),
        title=Text(title, style="bold green"),
        title_align="left",
        box=box.ROUNDED,
        border_style="green",
        padding=(1, 2),
        width=52,
    )


def _format_duration(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"
    unit = "second" if seconds == 1 else "seconds"
    return f"{seconds} {unit}"


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

    console.print()
    console.print(_banner("Deploy FastMCP on Horizon", style="magenta"))
    console.print()
    console.print(Text("✓ Device authorization started", style="bold green"))
    console.print()
    console.print("  Open this URL in your browser:")
    console.print()
    console.print(
        Padding(
            Text(authorization.verification_uri_complete, style="cyan underline"),
            (0, 2),
        )
    )
    console.print()
    console.print("  Confirm this code:")
    console.print()
    code = Table.grid()
    code.add_column(justify="center", width=52)
    code.add_row(Text(authorization.user_code, style="bold"))
    console.print(code)
    console.print()
    expires_in = _format_duration(authorization.expires_in)
    console.print(Text(f"The request expires in {expires_in}.", style="dim"))
    console.print(Text("Press Ctrl-C to cancel.", style="dim"))
    console.print()


def start_device_approval_status(*, json_output: bool) -> Status | None:
    """Start the terminal spinner while the browser approval is pending."""
    if json_output:
        return None
    status = console.status(
        "[cyan]Waiting for approval in your browser[/cyan]",
        spinner="dots",
        spinner_style="cyan",
    )
    status.start()
    return status


def stop_device_approval_status(status: Status | None) -> None:
    """Stop a device approval spinner when one is active."""
    if status is not None:
        status.stop()


def emit_identity(
    command: Literal["login", "whoami"],
    user: HorizonUser,
    *,
    json_output: bool,
) -> None:
    """Show the authenticated user."""
    if json_output:
        _write_json(
            {
                "ok": True,
                "command": command,
                "user": user.model_dump(mode="json"),
            }
        )
        return

    console.print()
    if command == "login":
        panel = _account_panel(
            user,
            title="Logged into Horizon",
            message="You are signed in to FastMCP.",
        )
    else:
        panel = _account_panel(
            user,
            title="Horizon Account",
            message="● Signed in",
        )
    console.print(panel)
    console.print()


def emit_environment_logout(*, json_output: bool) -> None:
    """Explain why logout cannot change an environment credential."""
    if json_output:
        _write_json(
            {
                "ok": True,
                "command": "logout",
                "credentialSource": "environment",
                "localCredentialRemoved": False,
                "remoteRevoked": False,
            }
        )
        return

    message = Group(
        Text("This session uses HORIZON_API_KEY.", style="bold"),
        Text("Remove it from your environment to sign out."),
        Text("No credential was revoked or removed.", style="dim"),
    )
    console.print()
    console.print(
        Panel(
            message,
            title=Text("Horizon Account", style="bold cyan"),
            title_align="left",
            box=box.ROUNDED,
            border_style="cyan",
            padding=(1, 2),
            width=60,
        )
    )
    console.print()


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
        title = "Logged out of Horizon"
        message = "The Horizon credential was revoked and removed from this device."
        style = "green"
    else:
        title = "Horizon Account"
        message = "No active Horizon credential remains on this device."
        style = "cyan"

    console.print()
    console.print(
        Panel(
            Text(message),
            title=Text(title, style=f"bold {style}"),
            title_align="left",
            box=box.ROUNDED,
            border_style=style,
            padding=(1, 2),
            width=60,
        )
    )
    console.print()


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

    titles = {
        "login": "✗ Sign in failed",
        "logout": "✗ Sign out failed",
        "whoami": "✗ Account lookup failed",
    }
    error_console.print()
    error_console.print(
        Panel(
            Text(message),
            title=Text(titles[command], style="bold red"),
            title_align="left",
            box=box.ROUNDED,
            border_style="red",
            padding=(1, 2),
            width=60,
        )
    )
    error_console.print()
