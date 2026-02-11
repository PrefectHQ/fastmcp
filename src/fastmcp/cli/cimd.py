"""CIMD (Client ID Metadata Document) CLI commands."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console

from fastmcp.server.auth.cimd import (
    CIMDFetcher,
    CIMDFetchError,
    CIMDValidationError,
)
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.cimd")
console = Console()


cimd_app = cyclopts.App(
    name="cimd",
    help="CIMD (Client ID Metadata Document) utilities for OAuth authentication.",
)


@cimd_app.command(name="create")
def create_command(
    *,
    name: Annotated[
        str,
        cyclopts.Parameter(help="Human-readable name of the client application"),
    ],
    redirect_uri: Annotated[
        list[str],
        cyclopts.Parameter(
            name=["--redirect-uri", "-r"],
            help="Allowed redirect URIs (can specify multiple)",
        ),
    ],
    client_id: Annotated[
        str | None,
        cyclopts.Parameter(
            name="--client-id",
            help="The URL where this document will be hosted (sets client_id directly)",
        ),
    ] = None,
    client_uri: Annotated[
        str | None,
        cyclopts.Parameter(
            name="--client-uri",
            help="URL of the client's home page",
        ),
    ] = None,
    logo_uri: Annotated[
        str | None,
        cyclopts.Parameter(
            name="--logo-uri",
            help="URL of the client's logo image",
        ),
    ] = None,
    scope: Annotated[
        str | None,
        cyclopts.Parameter(
            name="--scope",
            help="Space-separated list of scopes the client may request",
        ),
    ] = None,
    output: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Output file path (default: stdout)",
        ),
    ] = None,
    pretty: Annotated[
        bool,
        cyclopts.Parameter(
            help="Pretty-print JSON output",
        ),
    ] = True,
) -> None:
    """Generate a CIMD document for hosting.

    Create a Client ID Metadata Document that you can host at an HTTPS URL.
    The URL where you host this document becomes your client_id.

    Example:
        fastmcp cimd create --name "My App" -r "http://localhost:*/callback"

    After creating the document, host it at an HTTPS URL with a non-root path,
    for example: https://myapp.example.com/oauth/client.json
    """
    # Build the document
    doc = {
        "client_id": client_id or "https://YOUR-DOMAIN.com/path/to/client.json",
        "client_name": name,
        "redirect_uris": redirect_uri,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    }

    # Add optional fields
    if client_uri:
        doc["client_uri"] = client_uri
    if logo_uri:
        doc["logo_uri"] = logo_uri
    if scope:
        doc["scope"] = scope

    # Format output
    json_output = json.dumps(doc, indent=2) if pretty else json.dumps(doc)

    # Write output
    if output:
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(json_output)
            f.write("\n")
        console.print(f"[green]✓[/green] CIMD document written to {output}")
        if not client_id:
            console.print(
                "\n[yellow]Important:[/yellow] client_id is a placeholder. Update it to the URL where you will host this document, or re-run with --client-id."
            )
    else:
        print(json_output)
        if not client_id:
            # Print instructions to stderr so they don't interfere with piping
            stderr_console = Console(stderr=True)
            stderr_console.print(
                "\n[yellow]Important:[/yellow] client_id is a placeholder."
                " Update it to the URL where you will host this document,"
                " or re-run with --client-id."
            )


@cimd_app.command(name="validate")
def validate_command(
    url: Annotated[
        str,
        cyclopts.Parameter(help="URL of the CIMD document to validate"),
    ],
    *,
    timeout: Annotated[
        float,
        cyclopts.Parameter(
            name=["--timeout", "-t"],
            help="HTTP request timeout in seconds",
        ),
    ] = 10.0,
) -> None:
    """Validate a hosted CIMD document.

    Fetches the document from the given URL and validates:
    - URL is valid CIMD URL (HTTPS, non-root path)
    - Document is valid JSON
    - Document conforms to CIMD schema
    - client_id in document matches the URL

    Example:
        fastmcp cimd validate https://myapp.example.com/oauth/client.json
    """

    async def _validate() -> bool:
        fetcher = CIMDFetcher(timeout=timeout)

        # Check URL format first
        if not fetcher.is_cimd_client_id(url):
            console.print(f"[red]✗[/red] Invalid CIMD URL: {url}")
            console.print()
            console.print("CIMD URLs must:")
            console.print("  • Use HTTPS (not HTTP)")
            console.print("  • Have a non-root path (e.g., /client.json, not just /)")
            return False

        console.print(f"[blue]→[/blue] Fetching {url}...")

        try:
            doc = await fetcher.fetch(url)
        except CIMDFetchError as e:
            console.print(f"[red]✗[/red] Failed to fetch document: {e}")
            return False
        except CIMDValidationError as e:
            console.print(f"[red]✗[/red] Validation error: {e}")
            return False

        # Success - show document details
        console.print("[green]✓[/green] Valid CIMD document")
        console.print()
        console.print("[bold]Document details:[/bold]")
        console.print(f"  client_id: {doc.client_id}")
        console.print(f"  client_name: {doc.client_name or '(not set)'}")
        console.print(f"  token_endpoint_auth_method: {doc.token_endpoint_auth_method}")

        if doc.redirect_uris:
            console.print("  redirect_uris:")
            for uri in doc.redirect_uris:
                console.print(f"    • {uri}")
        else:
            console.print("  redirect_uris: (none)")

        if doc.scope:
            console.print(f"  scope: {doc.scope}")

        if doc.client_uri:
            console.print(f"  client_uri: {doc.client_uri}")

        return True

    success = asyncio.run(_validate())
    if not success:
        sys.exit(1)
