"""Shared utilities for install commands."""

import json
import ntpath
import os
import re
import string
import subprocess
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import ValidationError
from rich import print

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

logger = get_logger(__name__)

# Server names are passed as subprocess arguments to CLI tools like `claude`
# and `gemini`. On Windows these may resolve to .cmd/.bat wrappers that run
# through cmd.exe, where shell metacharacters (& | ; etc.) in arguments can
# cause command injection. Restrict names to safe characters.
_SAFE_NAME_RE = re.compile(r"^[\w\-. ]+$")


def validate_server_name(name: str) -> str:
    """Validate that a server name is safe for use as a subprocess argument.

    Raises SystemExit if the name contains shell metacharacters.
    """
    if not _SAFE_NAME_RE.fullmatch(name):
        print(
            f"[red]Invalid server name '[bold]{name}[/bold]': "
            "names may only contain letters, numbers, hyphens, underscores, dots, and spaces.[/red]"
        )
        sys.exit(1)
    return name


# Characters that cmd.exe passes through unchanged outside double quotes.
# Arguments containing anything else are quoted.
_BATCH_UNQUOTED_CHARS = frozenset(
    string.ascii_letters + string.digits + "#$*+-./:?@\\_"
)

# `%cd:~,%` is a zero-length substring of the always-defined `cd` variable.
# Writing each `%` as `%%cd:~,%` makes cmd.exe's percent expansion produce a
# literal `%` and stops it from pairing that `%` with any other one.
_BATCH_PERCENT = "%%cd:~,%"


def _quote_batch_text(text: str) -> str:
    """Quote text for cmd.exe and the C runtime argument parser."""
    quoted: list[str] = ['"']
    backslashes = 0
    for char in text:
        if char == "\\":
            backslashes += 1
            quoted.append(char)
            continue
        if char == '"':
            # 2n backslashes before a quote, then `""`, keeps n backslashes and
            # one literal quote while cmd.exe stays inside a quoted region.
            quoted.append("\\" * backslashes + '""')
        elif char == "%":
            quoted.append(_BATCH_PERCENT)
        else:
            quoted.append(char)
        backslashes = 0
    quoted.append("\\" * backslashes + '"')
    return "".join(quoted)


def quote_windows_batch_argument(argument: str) -> str:
    """Quote one argument for a command line that runs a `.cmd` or `.bat` file.

    The result reaches the program that the batch file launches as the
    original literal string. This follows the batch file argument quoting in
    Rust's standard library: arguments are wrapped in double quotes so cmd.exe
    treats `&`, `|`, `<`, `>`, `^`, parentheses and similar characters as
    text, including when the batch file expands them again through `%*`.
    Embedded quotes are doubled, which both cmd.exe and the Microsoft C
    runtime read as a literal quote. Each `%` is rewritten so cmd.exe does not
    expand environment variables. Delayed `!` expansion is disabled by the
    command processor options in `windows_batch_command_line`.

    Raises:
        ValueError: If the argument contains a line break. cmd.exe ends the
            command at a line break, so it cannot be passed literally.
    """
    if "\r" in argument or "\n" in argument:
        raise ValueError(
            "Arguments passed to a Windows .cmd or .bat command cannot contain line breaks"
        )
    needs_quotes = argument == "" or argument.endswith("\\")
    for char in argument:
        if char.isascii():
            if char not in _BATCH_UNQUOTED_CHARS:
                needs_quotes = True
        elif unicodedata.category(char) == "Cc":
            needs_quotes = True
    if not needs_quotes:
        return argument
    return _quote_batch_text(argument)


def windows_batch_command_line(command: Sequence[str]) -> str:
    """Build a cmd.exe command line that runs a batch file with literal arguments.

    `command[0]` is the batch file path and the remaining items are its
    arguments. The returned string is passed verbatim to `CreateProcess` with
    cmd.exe as the executable. Command extensions are enabled for the `%`
    handling, delayed expansion and AutoRun commands are disabled, and `/s`
    makes cmd.exe remove exactly the outer pair of quotes.

    Raises:
        ValueError: If the batch file path or an argument cannot be passed.
    """
    script, *arguments = command
    if '"' in script or script.endswith("\\") or "\r" in script or "\n" in script:
        raise ValueError(f"Invalid Windows batch file path: {script!r}")
    parts = [_quote_batch_text(script)]
    parts.extend(quote_windows_batch_argument(argument) for argument in arguments)
    return 'cmd.exe /e:on /v:off /d /s /c "' + " ".join(parts) + '"'


def _windows_command_processor() -> str:
    """Return the absolute path of cmd.exe."""
    comspec = os.environ.get("COMSPEC", "")
    if ntpath.isabs(comspec):
        return comspec
    system_root = os.environ.get("SYSTEMROOT", "C:\\Windows")
    return ntpath.join(system_root, "System32", "cmd.exe")


def run_cli_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a client CLI so that it receives each argument literally.

    On Windows, a `.cmd` or `.bat` file always runs through cmd.exe, which
    would otherwise interpret operators and `%` expansions in the arguments.
    For those files the command line is built with
    `windows_batch_command_line`. Other executables, and every executable on
    other platforms, receive the argument list directly.

    Raises:
        subprocess.CalledProcessError: If the command exits with an error.
        ValueError: If an argument cannot be passed to a batch file.
    """
    if sys.platform == "win32" and command[0].lower().endswith((".cmd", ".bat")):
        return subprocess.run(
            windows_batch_command_line(command),
            executable=_windows_command_processor(),
            check=True,
            capture_output=True,
            text=True,
        )
    return subprocess.run(command, check=True, capture_output=True, text=True)


def parse_env_var(env_var: str) -> tuple[str, str]:
    """Parse environment variable string in format KEY=VALUE."""
    if "=" not in env_var:
        print(
            f"[red]Invalid environment variable format: '[bold]{env_var}[/bold]'. Must be KEY=VALUE[/red]"
        )
        sys.exit(1)
    key, value = env_var.split("=", 1)
    if not key.strip():
        print(
            f"[red]Invalid environment variable format: '[bold]{env_var}[/bold]'. KEY cannot be empty[/red]"
        )
        sys.exit(1)
    return key.strip(), value.strip()


async def process_common_args(
    server_spec: str,
    server_name: str | None,
    with_packages: list[str] | None,
    env_vars: list[str] | None,
    env_file: Path | None,
) -> tuple[Path, str | None, str, list[str], dict[str, str] | None]:
    """Process common arguments shared by all install commands.

    Handles both fastmcp.json config files and traditional file.py:object syntax.
    """
    # Convert None to empty lists for list parameters
    with_packages = with_packages or []
    env_vars = env_vars or []
    # Create MCPServerConfig from server_spec
    config = None
    config_path: Path | None = None
    if server_spec.endswith(".json"):
        config_path = Path(server_spec).resolve()
        if not config_path.exists():
            print(f"[red]Configuration file not found: {config_path}[/red]")
            sys.exit(1)

        try:
            with open(config_path) as f:
                data = json.load(f)

            # Check if it's an MCPConfig (has mcpServers key)
            if "mcpServers" in data:
                # MCPConfig files aren't supported for install
                print("[red]MCPConfig files are not supported for installation[/red]")
                sys.exit(1)
            else:
                # It's a MCPServerConfig
                config = MCPServerConfig.from_file(config_path)

                # Merge packages from config if not overridden
                if config.environment.dependencies:
                    # Merge with CLI packages (CLI takes precedence)
                    config_packages = list(config.environment.dependencies)
                    with_packages = list(set(with_packages + config_packages))
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"[red]Invalid configuration file: {e}[/red]")
            sys.exit(1)
    else:
        # Create config from file path
        source = FileSystemSource(path=server_spec)
        config = MCPServerConfig(source=source)

    # Extract file and server_object from the source
    # The FileSystemSource handles parsing path:object syntax
    source_path = Path(config.source.path).expanduser()
    # If loaded from a JSON config, resolve relative paths against the config's directory
    if not source_path.is_absolute() and config_path is not None:
        file = (config_path.parent / source_path).resolve()
    else:
        file = source_path.resolve()
    # Update the source path so load_server() resolves correctly
    config.source.path = str(file)
    server_object = (
        config.source.entrypoint if hasattr(config.source, "entrypoint") else None
    )

    logger.debug(
        "Installing server",
        extra={
            "file": str(file),
            "server_name": server_name,
            "server_object": server_object,
            "with_packages": with_packages,
        },
    )

    # Verify the resolved file actually exists
    if not file.is_file():
        print(f"[red]Server file not found: {file}[/red]")
        sys.exit(1)

    # Try to import server to get its name and dependencies.
    # load_server() resolves paths against cwd, which may differ from our
    # config-relative resolution, so we catch SystemExit from its file check.
    name = server_name
    server = None
    if not name:
        try:
            server = await config.source.load_server()
            name = server.name
        except (ImportError, ModuleNotFoundError, SystemExit) as e:
            logger.debug(
                "Could not import server (likely missing dependencies), using file name",
                extra={"error": str(e)},
            )
            name = file.stem

    # Process environment variables if provided
    env_dict: dict[str, str] | None = None
    if env_file or env_vars:
        env_dict = {}
        # Load from .env file if specified
        if env_file:
            try:
                env_dict |= {
                    k: v for k, v in dotenv_values(env_file).items() if v is not None
                }
            except Exception as e:
                print(f"[red]Failed to load .env file: {e}[/red]")
                sys.exit(1)

        # Add command line environment variables
        for env_var in env_vars:
            key, value = parse_env_var(env_var)
            env_dict[key] = value

    return file, server_object, name, with_packages, env_dict


def open_deeplink(url: str, *, expected_scheme: str) -> bool:
    """Attempt to open a deeplink URL using the system's default handler.

    Args:
        url: The deeplink URL to open.
        expected_scheme: The URL scheme to validate (e.g. "cursor", "goose").

    Returns:
        True if the command succeeded, False otherwise.
    """
    parsed = urlparse(url)
    if parsed.scheme != expected_scheme:
        logger.warning(
            f"Invalid deeplink scheme: {parsed.scheme}, expected {expected_scheme}"
        )
        return False

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=True, capture_output=True)
        elif sys.platform == "win32":
            os.startfile(url)
        else:
            subprocess.run(["xdg-open", url], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
