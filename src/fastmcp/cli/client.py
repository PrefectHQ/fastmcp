"""Client-side CLI commands for querying and invoking MCP servers."""

import difflib
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import cyclopts
import mcp.types
from rich.console import Console

from fastmcp.cli.discovery import DiscoveredServer, discover_servers, resolve_name
from fastmcp.client.client import CallToolResult, Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports.base import ClientTransport
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.client.transports.stdio import StdioTransport
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.client")
console = Console()


# ---------------------------------------------------------------------------
# Server spec resolution
# ---------------------------------------------------------------------------

_JSON_SCHEMA_TYPE_MAP: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "null": "None",
}


def resolve_server_spec(
    server_spec: str | None,
    *,
    command: str | None = None,
    transport: str | None = None,
) -> str | dict[str, Any] | ClientTransport:
    """Turn CLI inputs into something ``Client()`` accepts.

    Exactly one of ``server_spec`` or ``command`` should be provided.

    Resolution order for ``server_spec``:
    1. URLs (``http://``, ``https://``) — passed through as-is.
       If ``--transport`` is ``sse``, the URL is rewritten to end with ``/sse``
       so ``infer_transport`` picks the right transport.
    2. Existing file paths, or strings ending in ``.py``/``.js``/``.json``.
    3. Anything else — name-based resolution via ``resolve_name``.

    When ``command`` is provided, the string is shell-split into a
    ``StdioTransport(command, args)``.
    """

    if command is not None and server_spec is not None:
        console.print(
            "[bold red]Error:[/bold red] Cannot use both a server spec and --command"
        )
        sys.exit(1)

    if command is not None:
        return _build_stdio_from_command(command)

    if server_spec is None:
        console.print(
            "[bold red]Error:[/bold red] Provide a server spec or use --command"
        )
        sys.exit(1)

    assert isinstance(server_spec, str)
    spec: str = server_spec

    # 1. URL
    if spec.startswith(("http://", "https://")):
        if transport == "sse" and not spec.rstrip("/").endswith("/sse"):
            spec = spec.rstrip("/") + "/sse"
        return spec

    # 2. File path (must be a file, not a directory)
    path = Path(spec)
    is_file = path.is_file() or (
        not path.is_dir() and spec.endswith((".py", ".js", ".json"))
    )

    if is_file:
        if spec.endswith(".json"):
            return _resolve_json_spec(path)
        if spec.endswith(".py"):
            # Run via `fastmcp run` so scripts don't need mcp.run()
            resolved_path = path.resolve()
            return StdioTransport(
                command="fastmcp",
                args=["run", str(resolved_path), "--no-banner"],
                log_file=Path(os.devnull),
            )
        # .js — pass through for Client's infer_transport
        return spec

    # 3. Name-based resolution (bare name or source:name)
    try:
        return resolve_name(spec)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


def _build_stdio_from_command(command_str: str) -> StdioTransport:
    """Shell-split a command string into a ``StdioTransport``."""
    try:
        parts = shlex.split(command_str)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] Invalid command: {exc}")
        sys.exit(1)

    if not parts:
        console.print("[bold red]Error:[/bold red] Empty --command")
        sys.exit(1)

    return StdioTransport(command=parts[0], args=parts[1:], log_file=Path(os.devnull))


def _resolve_json_spec(path: Path) -> str | dict[str, Any]:
    """Disambiguate a ``.json`` server spec."""

    if not path.exists():
        console.print(
            f"[bold red]Error:[/bold red] File not found: [cyan]{path}[/cyan]"
        )
        sys.exit(1)

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        console.print(f"[bold red]Error:[/bold red] Invalid JSON in {path}: {exc}")
        sys.exit(1)

    if isinstance(data, dict) and "mcpServers" in data:
        return data

    # Likely a fastmcp.json (MCPServerConfig) — not directly usable as a client target.
    console.print(
        f"[bold red]Error:[/bold red] [cyan]{path}[/cyan] is a FastMCP server config, not an MCPConfig.\n"
        f"Start the server first, then query it:\n\n"
        f"  fastmcp run {path}\n"
        f"  fastmcp list http://localhost:8000/mcp\n"
    )
    sys.exit(1)


def _is_http_target(resolved: str | dict[str, Any] | ClientTransport) -> bool:
    """Return True if the resolved target will use an HTTP-based transport.

    MCPConfig dicts are excluded because ``MCPConfigTransport`` manages
    individual server transports internally and does not support top-level auth.
    """
    if isinstance(resolved, str):
        return resolved.startswith(("http://", "https://"))
    return isinstance(resolved, (StreamableHttpTransport, SSETransport))


async def _terminal_elicitation_handler(
    message: str,
    response_type: type[Any] | None,
    params: Any,
    context: Any,
) -> ElicitResult[dict[str, Any]]:
    """Prompt the user on the terminal for elicitation responses.

    Prints the server's message and prompts for each field in the schema.
    The user can type 'decline' or 'cancel' instead of a value to abort.
    """
    from mcp.types import ElicitRequestFormParams

    console.print(f"\n[bold yellow]Server asks:[/bold yellow] {message}")

    if not isinstance(params, ElicitRequestFormParams):
        answer = console.input(
            "[dim](press Enter to accept, or type 'decline'):[/dim] "
        )
        if answer.strip().lower() == "decline":
            return ElicitResult(action="decline")
        if answer.strip().lower() == "cancel":
            return ElicitResult(action="cancel")
        return ElicitResult(action="accept", content={})

    schema = params.requestedSchema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    if not properties:
        answer = console.input(
            "[dim](press Enter to accept, or type 'decline'):[/dim] "
        )
        if answer.strip().lower() == "decline":
            return ElicitResult(action="decline")
        if answer.strip().lower() == "cancel":
            return ElicitResult(action="cancel")
        return ElicitResult(action="accept", content={})

    result: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        type_hint = field_schema.get("type", "string")
        req_marker = " [red]*[/red]" if field_name in required else ""
        prompt_text = f"  [cyan]{field_name}[/cyan] ({type_hint}){req_marker}: "

        raw = console.input(prompt_text)
        if raw.strip().lower() == "decline":
            return ElicitResult(action="decline")
        if raw.strip().lower() == "cancel":
            return ElicitResult(action="cancel")

        if raw == "" and field_name not in required:
            continue

        result[field_name] = coerce_value(raw, field_schema)

    return ElicitResult(action="accept", content=result)


def _build_client(
    resolved: str | dict[str, Any] | ClientTransport,
    *,
    timeout: float | None = None,
    auth: str | None = None,
) -> Client:
    """Build a ``Client`` from a resolved server spec.

    Applies ``auth='oauth'`` automatically for HTTP-based targets unless
    the caller explicitly passes ``--auth none`` to disable it.

    ``auth=None`` means "not specified" (use default), ``auth="none"``
    means "explicitly disabled".
    """
    if auth == "none":
        effective_auth: str | None = None
    elif auth is not None:
        effective_auth = auth
    elif _is_http_target(resolved):
        effective_auth = "oauth"
    else:
        effective_auth = None

    return Client(
        resolved,
        timeout=timeout,
        auth=effective_auth,
        elicitation_handler=_terminal_elicitation_handler,
    )


# ---------------------------------------------------------------------------
# Argument coercion
# ---------------------------------------------------------------------------


def coerce_value(raw: str, schema: dict[str, Any]) -> Any:
    """Coerce a string CLI value according to a JSON-Schema type hint."""

    schema_type = schema.get("type", "string")

    if schema_type == "integer":
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"Expected integer, got {raw!r}") from None

    if schema_type == "number":
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"Expected number, got {raw!r}") from None

    if schema_type == "boolean":
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"Expected boolean, got {raw!r}")

    if schema_type in ("array", "object"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(f"Expected JSON {schema_type}, got {raw!r}") from None

    # Default: treat as string
    return raw


def parse_tool_arguments(
    raw_args: tuple[str, ...],
    input_json: str | None,
    input_schema: dict[str, Any],
) -> dict[str, Any]:
    """Build a tool-call argument dict from CLI inputs.

    A single JSON object argument is treated as the full argument dict.
    ``--input-json`` provides the base dict; ``key=value`` pairs override.
    Values are coerced using the tool's ``inputSchema``.
    """

    # A single positional arg that looks like JSON → treat as input-json
    if len(raw_args) == 1 and raw_args[0].startswith("{") and input_json is None:
        input_json = raw_args[0]
        raw_args = ()

    result: dict[str, Any] = {}

    if input_json is not None:
        try:
            parsed = json.loads(input_json)
        except json.JSONDecodeError as exc:
            console.print(f"[bold red]Error:[/bold red] Invalid --input-json: {exc}")
            sys.exit(1)
        if not isinstance(parsed, dict):
            console.print(
                "[bold red]Error:[/bold red] --input-json must be a JSON object"
            )
            sys.exit(1)
        result.update(parsed)

    properties = input_schema.get("properties", {})

    for arg in raw_args:
        if "=" not in arg:
            console.print(
                f"[bold red]Error:[/bold red] Invalid argument [cyan]{arg}[/cyan] — expected key=value"
            )
            sys.exit(1)
        key, value = arg.split("=", 1)
        prop_schema = properties.get(key, {})
        try:
            result[key] = coerce_value(value, prop_schema)
        except ValueError as exc:
            console.print(
                f"[bold red]Error:[/bold red] Argument [cyan]{key}[/cyan]: {exc}"
            )
            sys.exit(1)

    return result


# ---------------------------------------------------------------------------
# Tool signature formatting
# ---------------------------------------------------------------------------


def _json_schema_type_to_str(schema: dict[str, Any]) -> str:
    """Produce a short Python-style type string from a JSON-Schema fragment."""

    if "anyOf" in schema:
        parts = [_json_schema_type_to_str(s) for s in schema["anyOf"]]
        return " | ".join(parts)

    schema_type = schema.get("type", "any")
    if isinstance(schema_type, list):
        return " | ".join(_JSON_SCHEMA_TYPE_MAP.get(t, t) for t in schema_type)

    return _JSON_SCHEMA_TYPE_MAP.get(schema_type, schema_type)


def format_tool_signature(tool: mcp.types.Tool) -> str:
    """Build ``name(param: type, ...) -> return_type`` from a tool's JSON schemas."""

    params: list[str] = []
    schema = tool.inputSchema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    for prop_name, prop_schema in properties.items():
        type_str = _json_schema_type_to_str(prop_schema)
        if prop_name in required:
            params.append(f"{prop_name}: {type_str}")
        else:
            default = prop_schema.get("default")
            default_repr = repr(default) if default is not None else "..."
            params.append(f"{prop_name}: {type_str} = {default_repr}")

    sig = f"{tool.name}({', '.join(params)})"

    if tool.outputSchema:
        ret = _json_schema_type_to_str(tool.outputSchema)
        sig += f" -> {ret}"

    return sig


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_schema(label: str, schema: dict[str, Any]) -> None:
    """Print a JSON schema with a label."""
    properties = schema.get("properties", {})
    if not properties:
        return
    console.print(f"    [dim]{label}: {json.dumps(schema)}[/dim]")


def _format_call_result_text(result: CallToolResult) -> None:
    """Pretty-print a tool call result to the console."""

    if result.is_error:
        for block in result.content:
            if isinstance(block, mcp.types.TextContent):
                console.print(f"[bold red]Error:[/bold red] {block.text}")
            else:
                console.print(f"[bold red]Error:[/bold red] {block}")
        return

    if result.structured_content is not None:
        console.print_json(json.dumps(result.structured_content))
        return

    for block in result.content:
        if isinstance(block, mcp.types.TextContent):
            console.print(block.text)
        elif isinstance(block, mcp.types.ImageContent):
            size = len(block.data) * 3 // 4  # rough decoded size
            console.print(f"[dim][Image: {block.mimeType}, ~{size} bytes][/dim]")
        elif isinstance(block, mcp.types.AudioContent):
            size = len(block.data) * 3 // 4
            console.print(f"[dim][Audio: {block.mimeType}, ~{size} bytes][/dim]")
        else:
            console.print(str(block))


def _content_block_to_dict(block: mcp.types.ContentBlock) -> dict[str, Any]:
    """Serialize a single content block to a JSON-safe dict."""
    if isinstance(block, mcp.types.TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, mcp.types.ImageContent):
        return {"type": "image", "mimeType": block.mimeType, "data": block.data}
    if isinstance(block, mcp.types.AudioContent):
        return {"type": "audio", "mimeType": block.mimeType, "data": block.data}
    return {"type": "unknown", "value": str(block)}


def _call_result_to_dict(result: CallToolResult) -> dict[str, Any]:
    """Serialize a ``CallToolResult`` to a JSON-safe dict."""

    content_list = [_content_block_to_dict(block) for block in result.content]
    out: dict[str, Any] = {"content": content_list, "is_error": result.is_error}
    if result.structured_content is not None:
        out["structured_content"] = result.structured_content
    return out


def _tools_to_json(tools: list[mcp.types.Tool]) -> list[dict[str, Any]]:
    """Serialize a list of tools to JSON-safe dicts."""

    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.inputSchema,
            **({"outputSchema": t.outputSchema} if t.outputSchema else {}),
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# Call handlers (tool, resource, prompt)
# ---------------------------------------------------------------------------


async def _handle_tool_call(
    client: Client,
    tool_name: str,
    arguments: tuple[str, ...],
    input_json: str | None,
    json_output: bool,
) -> None:
    """Handle a tool call within an open client session."""
    tools = await client.list_tools()
    tool_map = {t.name: t for t in tools}

    if tool_name not in tool_map:
        close_matches = difflib.get_close_matches(
            tool_name, tool_map.keys(), n=3, cutoff=0.5
        )
        msg = f"Tool [cyan]{tool_name}[/cyan] not found."
        if close_matches:
            suggestions = ", ".join(f"[cyan]{m}[/cyan]" for m in close_matches)
            msg += f" Did you mean: {suggestions}?"
        console.print(f"[bold red]Error:[/bold red] {msg}")
        sys.exit(1)

    tool = tool_map[tool_name]
    parsed_args = parse_tool_arguments(arguments, input_json, tool.inputSchema)

    required = set(tool.inputSchema.get("required", []))
    provided = set(parsed_args.keys())
    missing = required - provided
    if missing:
        missing_str = ", ".join(f"[cyan]{m}[/cyan]" for m in sorted(missing))
        console.print(
            f"[bold red]Error:[/bold red] Missing required arguments: {missing_str}"
        )
        console.print()
        sig = format_tool_signature(tool)
        console.print(f"  [dim]{sig}[/dim]")
        sys.exit(1)

    result = await client.call_tool(tool_name, parsed_args, raise_on_error=False)

    if json_output:
        console.print_json(json.dumps(_call_result_to_dict(result)))
    else:
        _format_call_result_text(result)

    if result.is_error:
        sys.exit(1)


async def _handle_resource(
    client: Client,
    uri: str,
    json_output: bool,
) -> None:
    """Handle a resource read within an open client session."""
    contents = await client.read_resource(uri)

    if json_output:
        data = []
        for block in contents:
            if isinstance(block, mcp.types.TextResourceContents):
                data.append(
                    {
                        "uri": str(block.uri),
                        "mimeType": block.mimeType,
                        "text": block.text,
                    }
                )
            elif isinstance(block, mcp.types.BlobResourceContents):
                data.append(
                    {
                        "uri": str(block.uri),
                        "mimeType": block.mimeType,
                        "blob": block.blob,
                    }
                )
        console.print_json(json.dumps(data))
        return

    for block in contents:
        if isinstance(block, mcp.types.TextResourceContents):
            console.print(block.text)
        elif isinstance(block, mcp.types.BlobResourceContents):
            size = len(block.blob) * 3 // 4
            console.print(f"[dim][Blob: {block.mimeType}, ~{size} bytes][/dim]")


async def _handle_prompt(
    client: Client,
    prompt_name: str,
    arguments: tuple[str, ...],
    input_json: str | None,
    json_output: bool,
) -> None:
    """Handle a prompt get within an open client session."""
    # Prompt arguments are always string->string, but we reuse
    # parse_tool_arguments for the key=value / --input-json parsing.
    # Pass an empty schema so values stay as strings.
    parsed_args = parse_tool_arguments(arguments, input_json, {"type": "object"})

    prompts = await client.list_prompts()
    prompt_map = {p.name: p for p in prompts}

    if prompt_name not in prompt_map:
        close_matches = difflib.get_close_matches(
            prompt_name, prompt_map.keys(), n=3, cutoff=0.5
        )
        msg = f"Prompt [cyan]{prompt_name}[/cyan] not found."
        if close_matches:
            suggestions = ", ".join(f"[cyan]{m}[/cyan]" for m in close_matches)
            msg += f" Did you mean: {suggestions}?"
        console.print(f"[bold red]Error:[/bold red] {msg}")
        sys.exit(1)

    result = await client.get_prompt(prompt_name, parsed_args or None)

    if json_output:
        data: dict[str, Any] = {}
        if result.description:
            data["description"] = result.description
        data["messages"] = [
            {
                "role": msg.role,
                "content": _content_block_to_dict(msg.content),
            }
            for msg in result.messages
        ]
        console.print_json(json.dumps(data))
        return

    for msg in result.messages:
        console.print(f"[bold]{msg.role}:[/bold]")
        if isinstance(msg.content, mcp.types.TextContent):
            console.print(f"  {msg.content.text}")
        elif isinstance(msg.content, mcp.types.ImageContent):
            size = len(msg.content.data) * 3 // 4
            console.print(
                f"  [dim][Image: {msg.content.mimeType}, ~{size} bytes][/dim]"
            )
        else:
            console.print(f"  {msg.content}")
        console.print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def list_command(
    server_spec: Annotated[
        str | None,
        cyclopts.Parameter(
            help="Server URL, Python file, MCPConfig JSON, or .js file",
        ),
    ] = None,
    *,
    command: Annotated[
        str | None,
        cyclopts.Parameter(
            "--command",
            help="Stdio command to connect to (e.g. 'npx -y @mcp/server')",
        ),
    ] = None,
    transport: Annotated[
        Literal["http", "sse"] | None,
        cyclopts.Parameter(
            name=["--transport", "-t"],
            help="Force transport type for URL targets (http or sse)",
        ),
    ] = None,
    resources: Annotated[
        bool,
        cyclopts.Parameter("--resources", help="Also list resources"),
    ] = False,
    prompts: Annotated[
        bool,
        cyclopts.Parameter("--prompts", help="Also list prompts"),
    ] = False,
    input_schema: Annotated[
        bool,
        cyclopts.Parameter("--input-schema", help="Show full input schemas"),
    ] = False,
    output_schema: Annotated[
        bool,
        cyclopts.Parameter("--output-schema", help="Show full output schemas"),
    ] = False,
    json_output: Annotated[
        bool,
        cyclopts.Parameter("--json", help="Output as JSON"),
    ] = False,
    timeout: Annotated[
        float | None,
        cyclopts.Parameter("--timeout", help="Connection timeout in seconds"),
    ] = None,
    auth: Annotated[
        str | None,
        cyclopts.Parameter(
            "--auth",
            help="Auth method: 'oauth', a bearer token string, or 'none' to disable",
        ),
    ] = None,
) -> None:
    """List tools available on an MCP server.

    Examples:
        fastmcp list http://localhost:8000/mcp
        fastmcp list server.py
        fastmcp list mcp.json --json
        fastmcp list --command 'npx -y @mcp/server' --resources
        fastmcp list http://server/mcp --transport sse
    """

    resolved = resolve_server_spec(server_spec, command=command, transport=transport)
    client = _build_client(resolved, timeout=timeout, auth=auth)

    try:
        async with client:
            tools = await client.list_tools()

            if json_output:
                data: dict[str, Any] = {"tools": _tools_to_json(tools)}
                if resources:
                    res = await client.list_resources()
                    data["resources"] = [
                        {
                            "uri": str(r.uri),
                            "name": r.name,
                            "description": r.description,
                            "mimeType": r.mimeType,
                        }
                        for r in res
                    ]
                if prompts:
                    prm = await client.list_prompts()
                    data["prompts"] = [
                        {
                            "name": p.name,
                            "description": p.description,
                            "arguments": [a.model_dump() for a in (p.arguments or [])],
                        }
                        for p in prm
                    ]
                console.print_json(json.dumps(data))
                return

            # Text output
            if not tools:
                console.print("[dim]No tools found.[/dim]")
            else:
                console.print(f"[bold]Tools ({len(tools)})[/bold]")
                console.print()
                for tool in tools:
                    sig = format_tool_signature(tool)
                    console.print(f"  [cyan]{sig}[/cyan]")
                    if tool.description:
                        console.print(f"    {tool.description}")
                    if input_schema:
                        _print_schema("Input", tool.inputSchema)
                    if output_schema and tool.outputSchema:
                        _print_schema("Output", tool.outputSchema)
                    console.print()

            if resources:
                res = await client.list_resources()
                console.print(f"[bold]Resources ({len(res)})[/bold]")
                console.print()
                if not res:
                    console.print("  [dim]No resources found.[/dim]")
                for r in res:
                    console.print(f"  [cyan]{r.uri}[/cyan]")
                    desc_parts = [r.name or "", r.description or ""]
                    desc = " — ".join(p for p in desc_parts if p)
                    if desc:
                        console.print(f"    {desc}")
                console.print()

            if prompts:
                prm = await client.list_prompts()
                console.print(f"[bold]Prompts ({len(prm)})[/bold]")
                console.print()
                if not prm:
                    console.print("  [dim]No prompts found.[/dim]")
                for p in prm:
                    args_str = ""
                    if p.arguments:
                        parts = [a.name for a in p.arguments]
                        args_str = f"({', '.join(parts)})"
                    console.print(f"  [cyan]{p.name}{args_str}[/cyan]")
                    if p.description:
                        console.print(f"    {p.description}")
                console.print()

    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


async def call_command(
    server_spec: Annotated[
        str | None,
        cyclopts.Parameter(
            help="Server URL, Python file, MCPConfig JSON, or .js file",
        ),
    ] = None,
    target: Annotated[
        str,
        cyclopts.Parameter(
            help="Tool name, resource URI, or prompt name (with --prompt)",
        ),
    ] = "",
    *arguments: str,
    command: Annotated[
        str | None,
        cyclopts.Parameter(
            "--command",
            help="Stdio command to connect to (e.g. 'npx -y @mcp/server')",
        ),
    ] = None,
    transport: Annotated[
        Literal["http", "sse"] | None,
        cyclopts.Parameter(
            name=["--transport", "-t"],
            help="Force transport type for URL targets (http or sse)",
        ),
    ] = None,
    prompt: Annotated[
        bool,
        cyclopts.Parameter("--prompt", help="Treat target as a prompt name"),
    ] = False,
    input_json: Annotated[
        str | None,
        cyclopts.Parameter(
            "--input-json",
            help="JSON string of arguments (merged with key=value args)",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        cyclopts.Parameter("--json", help="Output raw JSON result"),
    ] = False,
    timeout: Annotated[
        float | None,
        cyclopts.Parameter("--timeout", help="Connection timeout in seconds"),
    ] = None,
    auth: Annotated[
        str | None,
        cyclopts.Parameter(
            "--auth",
            help="Auth method: 'oauth', a bearer token string, or 'none' to disable",
        ),
    ] = None,
) -> None:
    """Call a tool, read a resource, or get a prompt on an MCP server.

    By default the target is treated as a tool name. If the target
    contains ``://`` it is treated as a resource URI. Pass ``--prompt``
    to treat it as a prompt name.

    Arguments are passed as key=value pairs. Use --input-json for complex
    or nested arguments.

    Examples:
        ```
        fastmcp call server.py greet name=World
        fastmcp call server.py resource://docs/readme
        fastmcp call server.py analyze --prompt data='[1,2,3]'
        fastmcp call http://server/mcp create --input-json '{"tags": ["a","b"]}'
        ```
    """

    if not target:
        console.print(
            "[bold red]Error:[/bold red] Missing target.\n\n"
            "Usage: fastmcp call <server> <target> [key=value ...]\n\n"
            "  target can be a tool name, a resource URI, or a prompt name (with --prompt).\n\n"
            "Use [cyan]fastmcp list <server>[/cyan] to see available tools."
        )
        sys.exit(1)

    resolved = resolve_server_spec(server_spec, command=command, transport=transport)
    client = _build_client(resolved, timeout=timeout, auth=auth)

    try:
        async with client:
            if prompt:
                await _handle_prompt(client, target, arguments, input_json, json_output)
            elif "://" in target:
                await _handle_resource(client, target, json_output)
            else:
                await _handle_tool_call(
                    client, target, arguments, input_json, json_output
                )

    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


async def discover_command(
    *,
    source: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            "--source",
            help="Only show servers from these sources (e.g. claude-code, cursor, gemini)",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        cyclopts.Parameter("--json", help="Output as JSON"),
    ] = False,
) -> None:
    """Discover MCP servers configured in editor and project configs.

    Scans Claude Desktop, Claude Code, Cursor, Gemini CLI, Goose, and
    project-level mcp.json files for MCP server definitions.

    Discovered server names can be used directly with ``fastmcp list``
    and ``fastmcp call`` instead of specifying a URL or file path.

    Examples:
        fastmcp discover
        fastmcp discover --source claude-code
        fastmcp discover --source cursor --source gemini --json
        fastmcp list weather
        fastmcp call cursor:weather get_forecast city=London
    """

    servers = discover_servers()

    if source:
        servers = [s for s in servers if s.source in source]

    if json_output:
        data: list[dict[str, Any]] = [
            {
                "name": s.name,
                "source": s.source,
                "qualified_name": s.qualified_name,
                "transport_summary": s.transport_summary,
                "config_path": str(s.config_path),
            }
            for s in servers
        ]
        console.print_json(json.dumps(data))
        return

    if not servers:
        console.print("[dim]No MCP servers found.[/dim]")
        console.print()
        console.print("Searched:")
        console.print("  • Claude Desktop config")
        console.print("  • ~/.claude.json (Claude Code)")
        console.print("  • .cursor/mcp.json (walked up from cwd)")
        console.print("  • ~/.gemini/settings.json (Gemini CLI)")
        console.print("  • ~/.config/goose/config.yaml (Goose)")
        console.print("  • ./mcp.json")
        return

    from rich.table import Table

    # Group by source
    by_source: dict[str, list[DiscoveredServer]] = {}
    for s in servers:
        by_source.setdefault(s.source, []).append(s)

    for source_name, group in by_source.items():
        console.print()
        console.print(f"[bold]Source:[/bold]  {source_name}")
        console.print(f"[bold]Config:[/bold]  [dim]{group[0].config_path}[/dim]")
        console.print()

        table = Table(
            show_header=True,
            header_style="bold",
            show_edge=False,
            pad_edge=False,
            box=None,
            padding=(0, 2),
        )
        table.add_column("Server", style="cyan")
        table.add_column("Transport", style="dim")

        for s in group:
            table.add_row(s.name, s.transport_summary)

        console.print(table)
        console.print()
