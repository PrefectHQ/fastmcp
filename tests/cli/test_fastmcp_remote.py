from pathlib import Path

import pytest

from fastmcp.client.auth import OAuth
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.tools import FunctionTool
from fastmcp.utilities.versions import VersionSpec
from fastmcp_remote.cli import (
    IgnoreTools,
    RemoteTransport,
    TransportStrategy,
    build_transport,
    normalize_transport,
    parse_args,
    parse_header,
    read_json_argument,
)


def sample_tool() -> str:
    return "ok"


def test_parse_header_accepts_spaced_value():
    assert parse_header("Authorization: Bearer token") == (
        "Authorization",
        "Bearer token",
    )


def test_parse_header_accepts_unspaced_value():
    assert parse_header("Authorization:Bearer token") == (
        "Authorization",
        "Bearer token",
    )


def test_parse_header_rejects_missing_colon():
    with pytest.raises(SystemExit):
        parse_args(["https://example.com/mcp", "--header", "Authorization"])


def test_read_json_argument_from_string():
    assert read_json_argument('{"client_id": "abc"}') == {"client_id": "abc"}


def test_read_json_argument_from_file(tmp_path: Path):
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text('{"scope": "read write"}')

    assert read_json_argument(f"@{metadata_file}") == {"scope": "read write"}


def test_http_requires_allow_http():
    with pytest.raises(SystemExit):
        parse_args(["http://localhost:8000/mcp"])


@pytest.mark.parametrize(
    ("strategy", "transport"),
    [
        ("http-first", "http"),
        ("http-only", "http"),
        ("sse-first", "sse"),
        ("sse-only", "sse"),
    ],
)
def test_transport_strategies_normalize_to_single_transport(
    strategy: TransportStrategy, transport: RemoteTransport
):
    assert normalize_transport(strategy) == transport


def test_auth_defaults_to_oauth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(["https://example.com/mcp"])

    transport = build_transport(config)

    assert isinstance(transport, StreamableHttpTransport)
    assert isinstance(transport.auth, OAuth)


def test_authorization_header_disables_oauth_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(
        [
            "https://example.com/mcp",
            "--header",
            "Authorization: Bearer token",
        ]
    )

    transport = build_transport(config)

    assert isinstance(transport, StreamableHttpTransport)
    assert transport.auth is None
    assert transport.headers == {"Authorization": "Bearer token"}


def test_explicit_oauth_keeps_oauth_with_authorization_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(
        [
            "https://example.com/mcp",
            "--header",
            "Authorization: Bearer token",
            "--auth",
            "oauth",
        ]
    )

    transport = build_transport(config)

    assert isinstance(transport.auth, OAuth)


def test_oauth_callback_options_pass_to_fastmcp_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(
        [
            "https://example.com/mcp",
            "8765",
            "--host",
            "127.0.0.1",
            "--auth-timeout",
            "12.5",
        ]
    )

    transport = build_transport(config)

    assert isinstance(transport.auth, OAuth)
    assert transport.auth.context.client_metadata.redirect_uris is not None
    assert str(transport.auth.context.client_metadata.redirect_uris[0]) == (
        "http://127.0.0.1:8765/callback"
    )
    assert transport.auth._callback_timeout == 12.5


def test_resource_isolates_token_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    default_config = parse_args(["https://example.com/mcp"])
    resource_config = parse_args(
        ["https://example.com/mcp", "--resource", "linear-prod"]
    )

    assert default_config.storage_dir == tmp_path
    assert resource_config.storage_dir.parent == tmp_path / "resources"
    assert resource_config.storage_dir != default_config.storage_dir


def test_sse_transport_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(["https://example.com/sse", "--transport", "sse-only"])

    transport = build_transport(config)

    assert isinstance(transport, SSETransport)


async def test_ignore_tools_transform_filters_matching_names():
    tool = FunctionTool.from_function(sample_tool, name="delete_user")
    transform = IgnoreTools(["delete*"])

    async def call_next(
        name: str, *, version: VersionSpec | None = None
    ) -> FunctionTool:
        return tool

    assert await transform.list_tools([tool]) == []
    assert await transform.get_tool("delete_user", call_next) is None
