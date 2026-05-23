from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import anyio
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)

from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.server import create_proxy
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec

RemoteTransport = Literal["http", "sse"]
AuthMode = Literal["oauth", "none"]
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class RemoteConfig:
    url: str
    headers: dict[str, str]
    transport: RemoteTransport
    auth: AuthMode | None
    callback_port: int | None
    callback_host: str
    callback_timeout: float
    storage_dir: Path
    ignore_tools: tuple[str, ...]
    show_banner: bool
    log_level: str | None


class IgnoreTools(Transform):
    def __init__(self, patterns: Sequence[str]) -> None:
        self.patterns = tuple(patterns)

    def _matches(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.patterns)

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if not self._matches(tool.name)]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        if self._matches(name):
            return None
        return await call_next(name, version=version)


def parse_header(value: str) -> tuple[str, str]:
    name, separator, header_value = value.partition(":")
    if not separator or not name.strip():
        raise argparse.ArgumentTypeError("Headers must use the format 'Name: Value'.")
    try:
        expanded_value = ENV_VAR_PATTERN.sub(
            lambda match: os.environ[match.group(1)], header_value
        )
    except KeyError as exc:
        raise argparse.ArgumentTypeError(
            f"Environment variable {exc.args[0]} is not set."
        ) from exc
    return name.strip(), expanded_value.strip()


def default_storage_dir(resource: str | None = None) -> Path:
    if config_dir := os.environ.get("FASTMCP_REMOTE_CONFIG_DIR"):
        base = Path(config_dir).expanduser()
    else:
        base = Path.home() / ".fastmcp" / "remote"
    if resource is None:
        return base
    digest = hashlib.sha256(resource.encode()).hexdigest()[:16]
    return base / "resources" / digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastmcp-remote",
        description="Bridge a remote MCP server to a local stdio MCP process.",
    )
    parser.add_argument("url", help="Remote MCP server URL.")
    parser.add_argument(
        "callback_port",
        nargs="?",
        type=int,
        help="OAuth callback port. Defaults to an available local port.",
    )
    parser.add_argument(
        "--transport",
        choices=["http", "sse"],
        default="http",
        help="Remote transport. Defaults to http.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        type=parse_header,
        help="Header to send upstream, in 'Name: Value' form. Repeat for multiple headers.",
    )
    parser.add_argument(
        "--auth",
        choices=["oauth", "none"],
        default=None,
        help="Authentication mode. Defaults to OAuth unless Authorization is provided.",
    )
    parser.add_argument(
        "--resource",
        help="Resource identifier used to isolate OAuth token storage.",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="OAuth callback hostname. Defaults to localhost.",
    )
    parser.add_argument(
        "--auth-timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the OAuth callback. Defaults to 300.",
    )
    parser.add_argument(
        "--ignore-tool",
        action="append",
        default=[],
        help="Hide tools matching this glob pattern. Repeat for multiple patterns.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress non-critical logs.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> RemoteConfig:
    parser = build_parser()
    args = parser.parse_args(argv)

    parsed_url = urlparse(args.url)
    if parsed_url.scheme not in {"http", "https"}:
        parser.error("The remote MCP server URL must start with http:// or https://.")

    headers = dict(args.header)
    if args.silent and args.debug:
        parser.error("--silent and --debug cannot be used together.")
    if args.auth_timeout <= 0:
        parser.error("--auth-timeout must be greater than 0.")

    log_level = "DEBUG" if args.debug else None
    if args.silent:
        log_level = "CRITICAL"

    return RemoteConfig(
        url=args.url,
        headers=headers,
        transport=args.transport,
        auth=args.auth,
        callback_port=args.callback_port,
        callback_host=args.host,
        callback_timeout=args.auth_timeout,
        storage_dir=default_storage_dir(args.resource),
        ignore_tools=tuple(args.ignore_tool),
        show_banner=not args.silent,
        log_level=log_level,
    )


def build_token_storage(storage_dir: Path) -> AsyncKeyValue:
    storage_dir.mkdir(parents=True, exist_ok=True)
    return FileTreeStore(
        data_directory=storage_dir,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(storage_dir),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
            storage_dir
        ),
    )


def resolve_auth(config: RemoteConfig) -> OAuth | None:
    authorization_header = any(
        name.lower() == "authorization" for name in config.headers
    )
    auth_mode = config.auth
    if auth_mode is None and authorization_header:
        auth_mode = "none"
    elif auth_mode is None:
        auth_mode = "oauth"

    if auth_mode == "none":
        return None

    return OAuth(
        token_storage=build_token_storage(config.storage_dir),
        callback_port=config.callback_port,
        callback_host=config.callback_host,
        callback_timeout=config.callback_timeout,
    )


def build_transport(config: RemoteConfig) -> SSETransport | StreamableHttpTransport:
    auth = resolve_auth(config)
    if config.transport == "sse":
        return SSETransport(config.url, headers=config.headers, auth=auth)
    return StreamableHttpTransport(config.url, headers=config.headers, auth=auth)


async def run(config: RemoteConfig) -> None:
    client = Client(build_transport(config))
    server = create_proxy(
        client,
        name="fastmcp-remote",
        provider_error_strategy="raise",
        validate_on_initialize=True,
    )
    if config.ignore_tools:
        server.add_transform(IgnoreTools(config.ignore_tools))
    await server.run_async(
        transport="stdio",
        show_banner=config.show_banner,
        log_level=config.log_level,
    )


def main(argv: Sequence[str] | None = None) -> None:
    config = parse_args(argv)
    anyio.run(run, config)
