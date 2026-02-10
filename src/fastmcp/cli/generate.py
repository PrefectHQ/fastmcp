"""Generate a standalone CLI script and agent skill from an MCP server."""

import keyword
import re
import sys
import textwrap
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import cyclopts
import mcp.types
import pydantic_core
from mcp import McpError
from rich.console import Console

from fastmcp.cli.client import _build_client, resolve_server_spec
from fastmcp.client.transports.base import ClientTransport
from fastmcp.client.transports.stdio import StdioTransport
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.generate")
console = Console()

# ---------------------------------------------------------------------------
# JSON Schema type → Python type string
# ---------------------------------------------------------------------------

_SIMPLE_TYPES = {"string", "integer", "number", "boolean", "null"}


def _is_simple_type(schema: dict[str, Any]) -> bool:
    """Check if a schema represents a simple (non-complex) type."""
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        # Union of types - simple only if all are simple
        return all(t in _SIMPLE_TYPES for t in schema_type)
    return schema_type in _SIMPLE_TYPES


def _is_simple_array(schema: dict[str, Any]) -> tuple[bool, str | None]:
    """Check if schema is an array of simple types.

    Returns (is_simple_array, item_type_str).
    """
    if schema.get("type") != "array":
        return False, None

    items = schema.get("items", {})
    if not _is_simple_type(items):
        return False, None

    # Map JSON Schema type to Python type
    item_type = items.get("type", "string")
    if isinstance(item_type, list):
        return False, None
    type_map = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }
    py_type = type_map.get(item_type)
    if py_type is None:
        return False, None
    return True, py_type


def _schema_to_python_type(schema: dict[str, Any]) -> tuple[str, bool]:
    """Convert a JSON Schema to a Python type annotation.

    Returns (type_annotation, needs_json_parsing).
    """
    # Check for simple array first
    is_simple_arr, item_type = _is_simple_array(schema)
    if is_simple_arr:
        return f"list[{item_type}]", False

    # Check for simple type
    if _is_simple_type(schema):
        schema_type = schema.get("type", "string")
        if isinstance(schema_type, list):
            # Union of simple types
            type_map = {
                "string": "str",
                "integer": "int",
                "number": "float",
                "boolean": "bool",
                "null": "None",
            }
            parts = [type_map.get(t, "str") for t in schema_type]
            return " | ".join(parts), False

        type_map = {
            "string": "str",
            "integer": "int",
            "number": "float",
            "boolean": "bool",
            "null": "None",
        }
        return type_map.get(schema_type, "str"), False

    # Complex type - needs JSON parsing
    return "str", True


def _format_schema_for_help(schema: dict[str, Any]) -> str:
    """Format a JSON schema for display in help text."""
    # Pretty print the schema, indented for help text
    schema_str = pydantic_core.to_json(schema, indent=2).decode()
    # Indent each line for help text alignment
    lines = schema_str.split("\n")
    indented = "\n                          ".join(lines)
    return f"JSON Schema: {indented}"


# ---------------------------------------------------------------------------
# Transport serialization
# ---------------------------------------------------------------------------


def serialize_transport(
    resolved: str | dict[str, Any] | ClientTransport,
) -> tuple[str, set[str]]:
    """Serialize a resolved transport to a Python expression string.

    Returns ``(expression, extra_imports)`` where *extra_imports* is a set of
    import lines needed by the expression.
    """
    if isinstance(resolved, str):
        return repr(resolved), set()

    if isinstance(resolved, StdioTransport):
        parts = [f"command={resolved.command!r}", f"args={resolved.args!r}"]
        if resolved.env:
            parts.append(f"env={resolved.env!r}")
        if resolved.cwd:
            parts.append(f"cwd={resolved.cwd!r}")
        expr = f"StdioTransport({', '.join(parts)})"
        imports = {"from fastmcp.client.transports import StdioTransport"}
        return expr, imports

    if isinstance(resolved, dict):
        return repr(resolved), set()

    # Fallback: try repr
    return repr(resolved), set()


# ---------------------------------------------------------------------------
# Per-tool code generation
# ---------------------------------------------------------------------------


def _to_python_identifier(name: str) -> str:
    """Sanitize a string into a valid Python identifier."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
    safe = safe or "_unnamed"
    if keyword.iskeyword(safe):
        safe = f"{safe}_"
    return safe


def _tool_function_source(tool: mcp.types.Tool) -> str:
    """Generate the source for a single ``@call_tool_app.command`` function."""
    schema = tool.inputSchema
    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))

    # Build parameter lines and track which need JSON parsing
    param_lines: list[str] = []
    call_args: list[str] = []
    json_params: list[tuple[str, str]] = []  # (prop_name, safe_name)
    seen_names: dict[str, str] = {}  # safe_name -> original prop_name

    for prop_name, prop_schema in properties.items():
        py_type, needs_json = _schema_to_python_type(prop_schema)
        help_text = prop_schema.get("description", "")
        is_required = prop_name in required
        safe_name = _to_python_identifier(prop_name)

        # Check for name collisions after sanitization
        if safe_name in seen_names:
            raise ValueError(
                f"Parameter name collision: '{prop_name}' and '{seen_names[safe_name]}' "
                f"both sanitize to '{safe_name}'"
            )
        seen_names[safe_name] = prop_name

        # For complex types, add schema to help text
        if needs_json:
            schema_help = _format_schema_for_help(prop_schema)
            help_text = f"{help_text}\\n{schema_help}" if help_text else schema_help
            json_params.append((prop_name, safe_name))

        # Escape special characters in help text
        help_escaped = (
            help_text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        )

        # Build parameter annotation
        if is_required:
            annotation = (
                f'Annotated[{py_type}, cyclopts.Parameter(help="{help_escaped}")]'
            )
            param_lines.append(f"    {safe_name}: {annotation},")
        else:
            default = prop_schema.get("default")
            if default is not None:
                # For complex types with defaults, serialize to JSON string
                if needs_json:
                    default_str = pydantic_core.to_json(default, fallback=str).decode()
                    annotation = f'Annotated[{py_type}, cyclopts.Parameter(help="{help_escaped}")]'
                    param_lines.append(
                        f"    {safe_name}: {annotation} = {default_str!r},"
                    )
                else:
                    annotation = f'Annotated[{py_type}, cyclopts.Parameter(help="{help_escaped}")]'
                    param_lines.append(f"    {safe_name}: {annotation} = {default!r},")
            else:
                # For list types, default to empty list; others default to None
                if py_type.startswith("list["):
                    annotation = f'Annotated[{py_type}, cyclopts.Parameter(help="{help_escaped}")]'
                    param_lines.append(f"    {safe_name}: {annotation} = [],")
                else:
                    annotation = f'Annotated[{py_type} | None, cyclopts.Parameter(help="{help_escaped}")]'
                    param_lines.append(f"    {safe_name}: {annotation} = None,")

        call_args.append(f"{prop_name!r}: {safe_name}")

    # Function name: sanitize to valid Python identifier
    fn_name = _to_python_identifier(tool.name)

    # Docstring - use single-quoted docstrings to avoid triple-quote escaping issues
    description = (tool.description or "").replace("\\", "\\\\").replace("'", "\\'")

    lines = []
    lines.append("")
    # Always pass name= to preserve the original tool name (cyclopts
    # would otherwise convert underscores to hyphens).
    lines.append(f"@call_tool_app.command(name={tool.name!r})")
    lines.append(f"async def {fn_name}(")

    if param_lines:
        lines.append("    *,")
        lines.extend(param_lines)

    lines.append(") -> None:")
    lines.append(f"    '''{description}'''")

    # Add JSON parsing for complex parameters
    if json_params:
        lines.append("    # Parse JSON parameters")
        for _prop_name, safe_name in json_params:
            lines.append(
                f"    {safe_name}_parsed = json.loads({safe_name}) if isinstance({safe_name}, str) else {safe_name}"
            )
        lines.append("")

    # Build call arguments, using parsed versions for JSON params
    call_arg_parts = []
    for prop_name, _ in properties.items():
        safe_name = _to_python_identifier(prop_name)
        if any(pn == prop_name for pn, _ in json_params):
            call_arg_parts.append(f"{prop_name!r}: {safe_name}_parsed")
        else:
            call_arg_parts.append(f"{prop_name!r}: {safe_name}")

    dict_items = ", ".join(call_arg_parts)
    lines.append(f"    await _call_tool({tool.name!r}, {{{dict_items}}})")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full script generation
# ---------------------------------------------------------------------------


def generate_cli_script(
    server_name: str,
    server_spec: str,
    transport_code: str,
    extra_imports: set[str],
    tools: list[mcp.types.Tool],
) -> str:
    """Generate the full CLI script source code."""

    # Determine app name from server_name - sanitize for use in string literal
    app_name = (
        server_name.replace(" ", "-").lower().replace("\\", "\\\\").replace('"', '\\"')
    )

    # --- Header ---
    lines: list[str] = []
    lines.append("#!/usr/bin/env python3")
    lines.append(f'"""CLI for {server_name} MCP server.')
    lines.append("")
    lines.append(f"Generated by: fastmcp generate-cli {server_spec}")
    lines.append('"""')
    lines.append("")

    # --- Imports ---
    lines.append("import json")
    lines.append("import sys")
    lines.append("from typing import Annotated")
    lines.append("")
    lines.append("import cyclopts")
    lines.append("import mcp.types")
    lines.append("from rich.console import Console")
    lines.append("")
    lines.append("from fastmcp import Client")
    for imp in sorted(extra_imports):
        lines.append(imp)
    lines.append("")

    # --- Transport config ---
    lines.append("# Modify this to change how the CLI connects to the MCP server.")
    lines.append(f"CLIENT_SPEC = {transport_code}")
    lines.append("")

    # --- App setup ---
    server_name_escaped = server_name.replace("\\", "\\\\").replace('"', '\\"')
    lines.append(
        f'app = cyclopts.App(name="{app_name}", help="CLI for {server_name_escaped} MCP server")'
    )
    lines.append(
        'call_tool_app = cyclopts.App(name="call-tool", help="Call a tool on the server")'
    )
    lines.append("app.command(call_tool_app)")
    lines.append("")
    lines.append("console = Console()")
    lines.append("")
    lines.append("")

    # --- Shared helpers ---
    lines.append(
        textwrap.dedent("""\
        # ---------------------------------------------------------------------------
        # Helpers
        # ---------------------------------------------------------------------------


        def _print_tool_result(result):
            if result.is_error:
                for block in result.content:
                    if isinstance(block, mcp.types.TextContent):
                        console.print(f"[bold red]Error:[/bold red] {block.text}")
                    else:
                        console.print(f"[bold red]Error:[/bold red] {block}")
                sys.exit(1)

            if result.structured_content is not None:
                console.print_json(json.dumps(result.structured_content))
                return

            for block in result.content:
                if isinstance(block, mcp.types.TextContent):
                    console.print(block.text)
                elif isinstance(block, mcp.types.ImageContent):
                    size = len(block.data) * 3 // 4
                    console.print(f"[dim][Image: {block.mimeType}, ~{size} bytes][/dim]")
                elif isinstance(block, mcp.types.AudioContent):
                    size = len(block.data) * 3 // 4
                    console.print(f"[dim][Audio: {block.mimeType}, ~{size} bytes][/dim]")


        async def _call_tool(tool_name: str, arguments: dict) -> None:
            # Filter out None values and empty lists (defaults for optional array params)
            filtered = {
                k: v
                for k, v in arguments.items()
                if v is not None and (not isinstance(v, list) or len(v) > 0)
            }
            async with Client(CLIENT_SPEC) as client:
                result = await client.call_tool(tool_name, filtered, raise_on_error=False)
                _print_tool_result(result)
                if result.is_error:
                    sys.exit(1)""")
    )
    lines.append("")
    lines.append("")

    # --- Generic commands ---
    lines.append(
        textwrap.dedent("""\
        # ---------------------------------------------------------------------------
        # List / read commands
        # ---------------------------------------------------------------------------


        @app.command
        async def list_tools() -> None:
            \"\"\"List available tools.\"\"\"
            async with Client(CLIENT_SPEC) as client:
                tools = await client.list_tools()
                if not tools:
                    console.print("[dim]No tools found.[/dim]")
                    return
                for tool in tools:
                    sig_parts = []
                    props = tool.inputSchema.get("properties", {})
                    required = set(tool.inputSchema.get("required", []))
                    for pname, pschema in props.items():
                        ptype = pschema.get("type", "string")
                        if pname in required:
                            sig_parts.append(f"{pname}: {ptype}")
                        else:
                            sig_parts.append(f"{pname}: {ptype} = ...")
                    sig = f"{tool.name}({', '.join(sig_parts)})"
                    console.print(f"  [cyan]{sig}[/cyan]")
                    if tool.description:
                        console.print(f"    {tool.description}")
                    console.print()


        @app.command
        async def list_resources() -> None:
            \"\"\"List available resources.\"\"\"
            async with Client(CLIENT_SPEC) as client:
                resources = await client.list_resources()
                if not resources:
                    console.print("[dim]No resources found.[/dim]")
                    return
                for r in resources:
                    console.print(f"  [cyan]{r.uri}[/cyan]")
                    desc_parts = [r.name or "", r.description or ""]
                    desc = " — ".join(p for p in desc_parts if p)
                    if desc:
                        console.print(f"    {desc}")
                console.print()


        @app.command
        async def read_resource(uri: Annotated[str, cyclopts.Parameter(help="Resource URI")]) -> None:
            \"\"\"Read a resource by URI.\"\"\"
            async with Client(CLIENT_SPEC) as client:
                contents = await client.read_resource(uri)
                for block in contents:
                    if isinstance(block, mcp.types.TextResourceContents):
                        console.print(block.text)
                    elif isinstance(block, mcp.types.BlobResourceContents):
                        size = len(block.blob) * 3 // 4
                        console.print(f"[dim][Blob: {block.mimeType}, ~{size} bytes][/dim]")


        @app.command
        async def list_prompts() -> None:
            \"\"\"List available prompts.\"\"\"
            async with Client(CLIENT_SPEC) as client:
                prompts = await client.list_prompts()
                if not prompts:
                    console.print("[dim]No prompts found.[/dim]")
                    return
                for p in prompts:
                    args_str = ""
                    if p.arguments:
                        parts = [a.name for a in p.arguments]
                        args_str = f"({', '.join(parts)})"
                    console.print(f"  [cyan]{p.name}{args_str}[/cyan]")
                    if p.description:
                        console.print(f"    {p.description}")
                console.print()


        @app.command
        async def get_prompt(
            name: Annotated[str, cyclopts.Parameter(help="Prompt name")],
            *arguments: str,
        ) -> None:
            \"\"\"Get a prompt by name. Pass arguments as key=value pairs.\"\"\"
            parsed: dict[str, str] = {}
            for arg in arguments:
                if "=" not in arg:
                    console.print(f"[bold red]Error:[/bold red] Invalid argument {arg!r} — expected key=value")
                    sys.exit(1)
                key, value = arg.split("=", 1)
                parsed[key] = value

            async with Client(CLIENT_SPEC) as client:
                result = await client.get_prompt(name, parsed or None)
                for msg in result.messages:
                    console.print(f"[bold]{msg.role}:[/bold]")
                    if isinstance(msg.content, mcp.types.TextContent):
                        console.print(f"  {msg.content.text}")
                    elif isinstance(msg.content, mcp.types.ImageContent):
                        size = len(msg.content.data) * 3 // 4
                        console.print(f"  [dim][Image: {msg.content.mimeType}, ~{size} bytes][/dim]")
                    else:
                        console.print(f"  {msg.content}")
                    console.print()""")
    )
    lines.append("")
    lines.append("")

    # --- Generated tool commands ---
    if tools:
        lines.append(
            "# ---------------------------------------------------------------------------"
        )
        lines.append("# Tool commands (generated from server schema)")
        lines.append(
            "# ---------------------------------------------------------------------------"
        )

        for tool in tools:
            lines.append(_tool_function_source(tool))

    # --- Entry point ---
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    app()")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill (SKILL.md) generation
# ---------------------------------------------------------------------------

_JSON_SCHEMA_TYPE_LABELS: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
    "array": "array",
    "object": "object",
}


def _param_to_cli_flag(prop_name: str) -> str:
    """Convert a JSON Schema property name to its CLI flag form.

    Replicates cyclopts' default_name_transform: camelCase → snake_case,
    lowercase, underscores → hyphens, strip leading/trailing hyphens.
    """
    safe = _to_python_identifier(prop_name)
    # camelCase / PascalCase → snake_case
    safe = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", safe)
    safe = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", safe)
    safe = safe.lower().replace("_", "-").strip("-")
    return f"--{safe}" if safe else "--arg"


def _schema_type_label(prop_schema: dict[str, Any]) -> str:
    """Return a human-readable type label for a property schema."""
    schema_type = prop_schema.get("type", "string")
    if isinstance(schema_type, list):
        labels = [_JSON_SCHEMA_TYPE_LABELS.get(t, t) for t in schema_type]
        return " | ".join(labels)

    label = _JSON_SCHEMA_TYPE_LABELS.get(schema_type, schema_type)

    # For arrays, include item type if simple
    if schema_type == "array":
        items = prop_schema.get("items", {})
        item_type = items.get("type", "")
        if isinstance(item_type, str) and item_type in _JSON_SCHEMA_TYPE_LABELS:
            return f"array[{item_type}]"

    return label


def _tool_skill_section(tool: mcp.types.Tool, cli_filename: str) -> str:
    """Generate a SKILL.md section for a single tool."""
    schema = tool.inputSchema
    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))

    # Build example invocation flags
    flag_parts_list: list[str] = []
    for p, p_schema in properties.items():
        flag = _param_to_cli_flag(p)
        schema_type = p_schema.get("type")
        is_bool = schema_type == "boolean" or (
            isinstance(schema_type, list) and "boolean" in schema_type
        )
        if is_bool:
            flag_parts_list.append(flag)
        else:
            flag_parts_list.append(f"{flag} <value>")
    flag_parts = " ".join(flag_parts_list)
    invocation = f"uv run --with fastmcp python {cli_filename} call-tool {tool.name}"
    if flag_parts:
        invocation += f" {flag_parts}"

    # Build parameter table rows
    rows: list[str] = []
    for prop_name, prop_schema in properties.items():
        flag = f"`{_param_to_cli_flag(prop_name)}`"
        type_label = _schema_type_label(prop_schema).replace("|", "\\|")
        is_required = "yes" if prop_name in required else "no"
        description = prop_schema.get("description", "")
        _, needs_json = _schema_to_python_type(prop_schema)
        if needs_json:
            description = (
                f"{description} (JSON string)" if description else "JSON string"
            )
        description = description.replace("\n", " ").replace("|", "\\|")
        rows.append(f"| {flag} | {type_label} | {is_required} | {description} |")

    param_table = ""
    if rows:
        header = "| Flag | Type | Required | Description |\n|------|------|----------|-------------|"
        param_table = f"\n{header}\n" + "\n".join(rows) + "\n"

    lines: list[str] = [f"### {tool.name}"]
    if tool.description:
        lines.extend(["", tool.description])
    lines.extend(["", "```bash", invocation, "```"])
    if param_table:
        lines.extend(["", param_table.strip("\n")])
    return "\n".join(lines)


def generate_skill_content(
    server_name: str,
    cli_filename: str,
    tools: list[mcp.types.Tool],
) -> str:
    """Generate a SKILL.md file for a generated CLI script."""
    skill_name = (
        server_name.replace(" ", "-").lower().replace("\\", "").replace('"', "")
    )
    safe_name = server_name.replace("\\", "").replace('"', "")
    description = f"CLI for the {safe_name} MCP server. Call tools, list resources, and get prompts."

    lines = [
        "---",
        f'name: "{skill_name}-cli"',
        f'description: "{description}"',
        "---",
        "",
        f"# {server_name} CLI",
        "",
    ]

    if tools:
        tool_bodies = "\n\n".join(
            _tool_skill_section(tool, cli_filename) for tool in tools
        )
        lines.extend(["## Tool Commands", "", tool_bodies, ""])

    lines.extend(
        [
            "## Utility Commands",
            "",
            "```bash",
            f"uv run --with fastmcp python {cli_filename} list-tools",
            f"uv run --with fastmcp python {cli_filename} list-resources",
            f"uv run --with fastmcp python {cli_filename} read-resource <uri>",
            f"uv run --with fastmcp python {cli_filename} list-prompts",
            f"uv run --with fastmcp python {cli_filename} get-prompt <name> [key=value ...]",
            "```",
            "",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


async def generate_cli_command(
    server_spec: Annotated[
        str,
        cyclopts.Parameter(
            help="Server URL, Python file, MCPConfig JSON, discovered name, or .js file",
        ),
    ],
    output: Annotated[
        str,
        cyclopts.Parameter(
            help="Output file path (default: cli.py)",
        ),
    ] = "cli.py",
    *,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["-f", "--force"],
            help="Overwrite output file if it exists",
        ),
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
    no_skill: Annotated[
        bool,
        cyclopts.Parameter(
            "--no-skill",
            help="Skip generating a SKILL.md agent skill alongside the CLI",
        ),
    ] = False,
) -> None:
    """Generate a standalone CLI script from an MCP server.

    Connects to the server, reads its tools/resources/prompts, and writes
    a Python script that can invoke them directly. Also generates a SKILL.md
    agent skill file unless --no-skill is passed.

    Examples:
        fastmcp generate-cli weather
        fastmcp generate-cli weather my_cli.py
        fastmcp generate-cli http://localhost:8000/mcp
        fastmcp generate-cli server.py output.py -f
        fastmcp generate-cli weather --no-skill
    """
    output_path = Path(output)
    skill_path = output_path.parent / "SKILL.md"

    # Check both files up front before doing any work
    existing: list[Path] = []
    if output_path.exists() and not force:
        existing.append(output_path)
    if not no_skill and skill_path.exists() and not force:
        existing.append(skill_path)
    if existing:
        names = ", ".join(f"[cyan]{p}[/cyan]" for p in existing)
        console.print(
            f"[bold red]Error:[/bold red] {names} already exist(s). "
            f"Use [cyan]-f[/cyan] to overwrite."
        )
        sys.exit(1)

    # Resolve the server spec to a transport
    resolved = resolve_server_spec(server_spec)
    transport_code, extra_imports = serialize_transport(resolved)

    # Derive a human-friendly server name from the spec
    server_name = _derive_server_name(server_spec)

    # Connect and discover capabilities
    client = _build_client(resolved, timeout=timeout, auth=auth)

    try:
        async with client:
            tools = await client.list_tools()
            console.print(
                f"[dim]Discovered {len(tools)} tool(s) from {server_spec}[/dim]"
            )

    except (RuntimeError, TimeoutError, McpError, OSError) as exc:
        console.print(f"[bold red]Error:[/bold red] Could not connect: {exc}")
        sys.exit(1)

    # Generate and write the script
    script = generate_cli_script(
        server_name=server_name,
        server_spec=server_spec,
        transport_code=transport_code,
        extra_imports=extra_imports,
        tools=tools,
    )

    output_path.write_text(script)
    output_path.chmod(output_path.stat().st_mode | 0o111)  # make executable

    console.print(
        f"[green]✓[/green] Wrote [cyan]{output_path}[/cyan] "
        f"with {len(tools)} tool command(s)"
    )

    if not no_skill:
        skill_content = generate_skill_content(
            server_name=server_name,
            cli_filename=output_path.name,
            tools=tools,
        )
        skill_path.write_text(skill_content)
        console.print(f"[green]✓[/green] Wrote [cyan]{skill_path}[/cyan]")

    console.print(f"[dim]Run: python {output_path} --help[/dim]")


def _derive_server_name(server_spec: str) -> str:
    """Derive a human-friendly name from a server spec."""
    # URL — use hostname
    if server_spec.startswith(("http://", "https://")):
        parsed = urlparse(server_spec)
        return parsed.hostname or "server"

    # File path — use stem
    if server_spec.endswith((".py", ".js", ".json")):
        return Path(server_spec).stem

    # Bare name or qualified name
    if ":" in server_spec:
        name = server_spec.split(":", 1)[1]
        return name or server_spec.split(":", 1)[0]

    return server_spec
