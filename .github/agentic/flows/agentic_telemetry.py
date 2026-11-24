"""Send the triage stack's OpenTelemetry spans to Logfire.

Prefect already traces flow and task runs, and fastmcp traces MCP calls, through
the OpenTelemetry API. Without a configured provider, those spans are dropped.
Every deployment entrypoint and the memory server import this module. In a flow run it configures
Logfire with the write token from the `fastmcp-triage-logfire-token` Secret block,
or from `LOGFIRE_TOKEN` (the memory server on Horizon sets that, with
`LOGFIRE_SERVICE_NAME=fastmcp-triage-memory`). When neither is there it does nothing. Telemetry never
stops a run.
"""

from __future__ import annotations

import os

SECRET = "fastmcp-triage-logfire-token"
SERVICE = os.environ.get("LOGFIRE_SERVICE_NAME", "fastmcp-triage")


def token() -> str | None:
    if os.environ.get("LOGFIRE_TOKEN"):
        return os.environ["LOGFIRE_TOKEN"]
    if not os.environ.get("PREFECT__FLOW_RUN_ID"):
        return None
    from prefect.blocks.system import Secret

    try:
        secret = Secret.load(SECRET, _sync=True)
        return str(secret.get())  # ty: ignore[unresolved-attribute]
    except Exception:  # noqa: BLE001 - a missing block or an API hiccup means no telemetry
        return None


def configure(service: str = SERVICE) -> bool:
    value = token()
    if not value:
        return False
    import logfire

    logfire.configure(
        token=value,
        service_name=service,
        environment="production",
        console=False,
        inspect_arguments=False,
    )
    return True


configure()
