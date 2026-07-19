from pathlib import Path

import pytest

from fastmcp.client.auth import OAuth
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.tools import FunctionTool
from fastmcp.utilities.versions import VersionSpec
from fastmcp_remote.cli import (
    IgnoreTools,
    build_transport,
    parse_args,
    parse_header,
    parse_verify,
)


def sample_tool() -> str:
    return "ok"


def test_parse_header_accepts_spaced_value():
    assert parse_header("Authorization: Bearer token") == (
        "Authorization",
        "Bearer token",
    )


def test_parse_header_preserves_spaces_inside_value():
    assert parse_header("X-Client-Name: My MCP Host") == (
        "X-Client-Name",
        "My MCP Host",
    )


def test_parse_header_preserves_colons_inside_value():
    assert parse_header("X-Callback-Url: https://example.com/oauth/callback") == (
        "X-Callback-Url",
        "https://example.com/oauth/callback",
    )


def test_parse_header_expands_environment_variables_in_value(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AUTH_HEADER", "Bearer token with spaces")

    assert parse_header("Authorization:${AUTH_HEADER}") == (
        "Authorization",
        "Bearer token with spaces",
    )


def test_parse_header_rejects_missing_environment_variable():
    with pytest.raises(SystemExit):
        parse_args(["https://example.com/mcp", "--header", "Authorization:${MISSING}"])


def test_parse_header_accepts_unspaced_value():
    assert parse_header("Authorization:Bearer token") == (
        "Authorization",
        "Bearer token",
    )


def test_parse_header_rejects_missing_colon():
    with pytest.raises(SystemExit):
        parse_args(["https://example.com/mcp", "--header", "Authorization"])


def test_http_urls_are_allowed():
    config = parse_args(["http://localhost:8000/mcp", "--auth", "none"])

    assert config.url == "http://localhost:8000/mcp"


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
    config = parse_args(["https://example.com/sse", "--transport", "sse"])

    transport = build_transport(config)

    assert isinstance(transport, SSETransport)


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_parse_verify_disables_verification(value: str):
    assert parse_verify(value) is False


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
def test_parse_verify_enables_verification(value: str):
    assert parse_verify(value) is True


def test_parse_verify_treats_other_values_as_ca_bundle_path():
    assert parse_verify("/etc/ssl/ca-bundle.pem") == "/etc/ssl/ca-bundle.pem"


def test_verify_defaults_to_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(["https://example.com/mcp", "--auth", "none"])

    assert config.verify is None
    assert build_transport(config).verify is None


def test_verify_false_disables_verification_on_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(
        ["https://example.com/mcp", "--auth", "none", "--verify", "false"]
    )

    transport = build_transport(config)

    assert config.verify is False
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.verify is False


def test_verify_ca_bundle_path_passes_to_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    ca_bundle = "/etc/ssl/custom-ca.pem"
    config = parse_args(
        ["https://example.com/mcp", "--auth", "none", "--verify", ca_bundle]
    )

    assert build_transport(config).verify == ca_bundle


def test_verify_passes_to_sse_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", str(tmp_path))
    config = parse_args(
        [
            "https://example.com/sse",
            "--transport",
            "sse",
            "--auth",
            "none",
            "--verify",
            "false",
        ]
    )

    transport = build_transport(config)

    assert isinstance(transport, SSETransport)
    assert transport.verify is False


async def test_ignore_tools_transform_filters_matching_names():
    tool = FunctionTool.from_function(sample_tool, name="delete_user")
    transform = IgnoreTools(["delete*"])

    async def call_next(
        name: str, *, version: VersionSpec | None = None
    ) -> FunctionTool:
        return tool

    assert await transform.list_tools([tool]) == []
    assert await transform.get_tool("delete_user", call_next) is None
