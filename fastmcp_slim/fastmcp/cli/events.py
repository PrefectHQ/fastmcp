"""`fastmcp events`: list a server's event types and watch one as it fires."""

import json
import sys
from contextlib import aclosing
from typing import Annotated, Any, Literal

import cyclopts
from rich.console import Console

from fastmcp.cli import client as client_cli
from fastmcp.experimental.events import list_events, subscribe_events
from fastmcp.experimental.events.models import EventOccurrence, EventType

console = Console()
# Status lines go to stderr so `watch --json` output stays one event per line.
status = Console(stderr=True)

events_app = cyclopts.App(
    name="events",
    help="Subscribe to a server's events (the MCP Events extension, SEP-3415)",
)

ServerSpec = Annotated[
    str | None,
    cyclopts.Parameter(help="Server URL, Python file, MCPConfig JSON, or .js file"),
]
Command = Annotated[
    str | None,
    cyclopts.Parameter(
        "--command", help="Stdio command to connect to (e.g. 'npx -y @mcp/server')"
    ),
]
Transport = Annotated[
    Literal["http", "sse"] | None,
    cyclopts.Parameter(
        name=["--transport", "-t"],
        help="Force transport type for URL targets (http or sse)",
    ),
]
Timeout = Annotated[
    float | None,
    cyclopts.Parameter("--timeout", help="Connection timeout in seconds"),
]
Auth = Annotated[
    str | None,
    cyclopts.Parameter(
        "--auth",
        help="Auth method: 'oauth', a bearer token string, or 'none' to disable",
    ),
]
JsonOutput = Annotated[bool, cyclopts.Parameter("--json", help="Output JSON")]


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {message}")
    sys.exit(1)


def _require_events(client: Any) -> None:
    capabilities = client.server_capabilities
    extensions = capabilities.extensions if capabilities else None
    if not extensions or "io.modelcontextprotocol/events" not in extensions:
        _fail("The server does not advertise the Events extension")


# Everything below that comes from the server is untrusted terminal output.
_safe = client_cli._sanitize_untrusted_text


def _describe(event_type: EventType) -> None:
    delivery = ", ".join(event_type.delivery)
    console.print(f"  [cyan]{_safe(event_type.name)}[/cyan] [dim]({delivery})[/dim]")
    if event_type.description:
        console.print(f"    {_safe(event_type.description)}")
    properties = (event_type.input_schema or {}).get("properties") or {}
    for name, schema in properties.items():
        kind = client_cli._json_schema_type_to_str(schema)
        console.print(f"    [dim]{_safe(name)}: {_safe(kind)}[/dim]")


@events_app.command(name="list")
async def list_events_command(
    server_spec: ServerSpec = None,
    *,
    command: Command = None,
    transport: Transport = None,
    json_output: JsonOutput = False,
    timeout: Timeout = None,
    auth: Auth = None,
) -> None:
    """List the event types a server offers.

    Examples:
        ```
        fastmcp events list server.py
        fastmcp events list http://localhost:8000/mcp --json
        ```
    """
    resolved = client_cli.resolve_server_spec(
        server_spec, command=command, transport=transport
    )
    try:
        client = client_cli._build_client(
            resolved, timeout=timeout, auth=auth, events=True
        )
        async with client:
            _require_events(client)
            event_types = await list_events(client)
    except Exception as exc:
        _fail(str(exc))
        return

    if json_output:
        listing = [
            event_type.model_dump(mode="json", by_alias=True, exclude_none=True)
            for event_type in event_types
        ]
        print(json.dumps({"events": listing}, indent=2))
        return
    console.print(f"[bold]Events ({len(event_types)})[/bold]")
    for event_type in event_types:
        _describe(event_type)


def _print_occurrence(event: EventOccurrence, cursor: str | None) -> None:
    console.print(
        f"[green]{_safe(event.timestamp)}[/green] "
        f"[cyan]{_safe(event.name)}[/cyan] {_safe(event.event_id)}"
    )
    payload = json.dumps(event.data, indent=2, ensure_ascii=False)
    console.print(_safe(payload), highlight=False)
    if cursor is not None:
        console.print(f"[dim]cursor {_safe(cursor)}[/dim]", highlight=False)


@events_app.command(name="watch")
async def watch_command(
    server_spec: ServerSpec = None,
    name: Annotated[str, cyclopts.Parameter(help="Event type to subscribe to")] = "",
    *arguments: str,
    command: Command = None,
    transport: Transport = None,
    input_json: Annotated[
        str | None,
        cyclopts.Parameter(
            "--input-json",
            help="JSON object of subscription arguments (merged with key=value args)",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        cyclopts.Parameter(
            "--cursor", help="Resume from this cursor instead of starting from now"
        ),
    ] = None,
    max_age: Annotated[
        float | None,
        cyclopts.Parameter(
            "--max-age", help="With --cursor, replay at most this many seconds"
        ),
    ] = None,
    delivery: Annotated[
        Literal["poll", "push"] | None,
        cyclopts.Parameter(
            "--delivery", help="Force poll or push (default: push when offered)"
        ),
    ] = None,
    count: Annotated[
        int | None,
        cyclopts.Parameter("--count", help="Exit after this many events"),
    ] = None,
    min_poll_interval: Annotated[
        float,
        cyclopts.Parameter(
            "--min-poll-interval", help="Shortest wait between polls, in seconds"
        ),
    ] = 1.0,
    json_output: Annotated[
        bool,
        cyclopts.Parameter("--json", help="Print one JSON object per event"),
    ] = False,
    timeout: Timeout = None,
    auth: Auth = None,
) -> None:
    """Subscribe to an event type and print each event as it arrives.

    Arguments are key=value pairs, coerced with the event type's input
    schema. The watch runs until interrupted (or `--count` events), then
    prints the cursor to resume from.

    Examples:
        ```
        fastmcp events watch server.py alert.fired
        fastmcp events watch server.py alert.fired severity=P1 --json
        fastmcp events watch http://localhost:8000/mcp alert.fired --cursor 42
        ```
    """
    if not name:
        _fail(
            "Missing event name.\n\n"
            "Usage: fastmcp events watch <server> <event> [key=value ...]\n\n"
            "Use [cyan]fastmcp events list <server>[/cyan] to see event types."
        )
    resolved = client_cli.resolve_server_spec(
        server_spec, command=command, transport=transport
    )
    subscription = None
    seen = 0
    try:
        client = client_cli._build_client(
            resolved, timeout=timeout, auth=auth, events=True
        )
        async with client:
            _require_events(client)
            event_types = {t.name: t for t in await list_events(client)}
            event_type = event_types.get(name)
            if event_type is None:
                available = ", ".join(_safe(t) for t in sorted(event_types))
                _fail(
                    f"Unknown event [cyan]{_safe(name)}[/cyan]. "
                    f"Available: {available or 'none'}"
                )
                return
            subscription_args = client_cli.parse_tool_arguments(
                arguments, input_json, event_type.input_schema or {}
            )
            subscription = subscribe_events(
                client,
                name,
                subscription_args or None,
                cursor=cursor,
                max_age_ms=None if max_age is None else int(max_age * 1000),
                min_poll_interval=min_poll_interval,
                delivery=delivery,
            )
            status.print(f"[dim]watching {name}; Ctrl-C to stop[/dim]")
            async with aclosing(subscription.__aiter__()) as events:
                async for event in events:
                    seen += 1
                    if json_output:
                        record = event.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        )
                        print(json.dumps({**record, "cursor": subscription.cursor}))
                        sys.stdout.flush()
                    else:
                        _print_occurrence(event, subscription.cursor)
                    if count is not None and seen >= count:
                        break
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _fail(str(exc))
    finally:
        if subscription is not None:
            if subscription.truncated:
                status.print("[yellow]The server skipped events (truncated)[/yellow]")
            if subscription.cursor is not None:
                status.print(
                    f"[dim]{seen} event(s); resume with --cursor "
                    f"{_safe(subscription.cursor)}[/dim]",
                    highlight=False,
                )
