"""Tests for writing HTTP and SSE transports into a generated CLI."""

import datetime
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli import generate as generate_module
from fastmcp.cli.generate import generate_cli_command, serialize_transport
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import (
    FastMCPTransport,
    SSETransport,
    StreamableHttpTransport,
)

REMOTE_TRANSPORTS = [StreamableHttpTransport, SSETransport]


def _build_test_server() -> FastMCP:
    server = FastMCP("Weather")

    @server.tool
    def forecast(city: str) -> str:
        """Forecast for a city."""
        return f"sunny in {city}"

    return server


def _rebuild_transport(code: str, imports: set[str]) -> Any:
    """Evaluate a serialized transport expression together with its imports."""
    namespace: dict[str, Any] = {}
    exec("\n".join(imports), namespace)
    return eval(code, namespace)


class TestSerializeRemoteTransport:
    @pytest.mark.parametrize(
        "transport_cls, url",
        [
            (StreamableHttpTransport, "https://weather.example.com/mcp"),
            (SSETransport, "https://weather.example.com/sse"),
        ],
    )
    def test_url(self, transport_cls: type, url: str):
        code, imports = serialize_transport(transport_cls(url))
        assert code == f"{transport_cls.__name__}(url={url!r})"
        assert imports == {
            f"from fastmcp.client.transports import {transport_cls.__name__}"
        }
        rebuilt = _rebuild_transport(code, imports)
        assert type(rebuilt) is transport_cls
        assert rebuilt.url == url
        assert rebuilt.headers == {}
        assert rebuilt.auth is None

    @pytest.mark.parametrize("transport_cls", REMOTE_TRANSPORTS)
    def test_headers(self, transport_cls: type):
        headers = {"X-Api-Key": "abc", "X-Team": "weather"}
        transport = transport_cls("https://weather.example.com/mcp", headers=headers)
        code, imports = serialize_transport(transport)
        assert f"headers={headers!r}" in code
        assert _rebuild_transport(code, imports).headers == headers

    def test_sse_read_timeout(self):
        transport = SSETransport("https://weather.example.com/sse", sse_read_timeout=90)
        code, imports = serialize_transport(transport)
        rebuilt = _rebuild_transport(code, imports)
        assert rebuilt.sse_read_timeout == datetime.timedelta(seconds=90)

    @pytest.mark.parametrize("transport_cls", REMOTE_TRANSPORTS)
    def test_oauth(self, transport_cls: type):
        transport = transport_cls("https://weather.example.com/mcp", auth="oauth")
        code, imports = serialize_transport(transport)
        assert "auth='oauth'" in code
        assert isinstance(_rebuild_transport(code, imports).auth, OAuth)

    @pytest.mark.parametrize("transport_cls", REMOTE_TRANSPORTS)
    def test_bearer_token(self, transport_cls: type):
        transport = transport_cls("https://weather.example.com/mcp", auth="tok-123")
        code, imports = serialize_transport(transport)
        assert "auth='tok-123'" in code
        auth = _rebuild_transport(code, imports).auth
        assert isinstance(auth, BearerAuth)
        assert auth.token.get_secret_value() == "tok-123"

    @pytest.mark.parametrize("transport_cls", REMOTE_TRANSPORTS)
    def test_custom_auth_raises(self, transport_cls: type):
        transport = transport_cls(
            "https://weather.example.com/mcp", auth=httpx2.BasicAuth("user", "pass")
        )
        with pytest.raises(ValueError, match="BasicAuth"):
            serialize_transport(transport)

    def test_unsupported_transport_raises(self):
        with pytest.raises(ValueError, match="FastMCPTransport"):
            serialize_transport(FastMCPTransport(FastMCP("Weather")))


class TestGenerateCliForNamedRemoteServer:
    """`fastmcp generate-cli <name>` for a URL entry in a discovered config."""

    async def _generate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        entry: dict[str, Any],
    ) -> Any:
        """Generate a CLI for the server "weather" and return its CLIENT_SPEC."""
        (tmp_path / "mcp.json").write_text(
            json.dumps({"mcpServers": {"weather": entry}}), encoding="utf-8"
        )
        monkeypatch.setattr("fastmcp.cli.discovery.Path.home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        output = tmp_path / "cli.py"
        with patch.object(
            generate_module, "_build_client", return_value=Client(_build_test_server())
        ):
            await generate_cli_command("weather", str(output), no_skill=True)

        script = output.read_text(encoding="utf-8")
        namespace: dict[str, Any] = {"__name__": "generated_cli"}
        exec(compile(script, str(output), "exec"), namespace)
        return namespace["CLIENT_SPEC"]

    @pytest.mark.parametrize(
        "entry, transport_cls",
        [
            (
                {
                    "url": "https://weather.example.com/mcp",
                    "headers": {"X-Api-Key": "abc"},
                },
                StreamableHttpTransport,
            ),
            (
                {"type": "http", "url": "https://weather.example.com/api"},
                StreamableHttpTransport,
            ),
            ({"url": "https://weather.example.com/sse"}, SSETransport),
        ],
    )
    async def test_script_connects_to_the_configured_server(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        entry: dict[str, Any],
        transport_cls: type,
    ):
        spec = await self._generate(tmp_path, monkeypatch, entry)
        assert type(spec) is transport_cls
        assert spec.url == entry["url"]
        assert spec.headers == entry.get("headers", {})

    async def test_script_keeps_oauth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        entry = {"url": "https://weather.example.com/mcp", "auth": "oauth"}
        spec = await self._generate(tmp_path, monkeypatch, entry)
        assert isinstance(spec.auth, OAuth)

    async def test_untranslatable_transport_exits_without_writing(self, tmp_path: Path):
        output = tmp_path / "cli.py"
        transport = FastMCPTransport(_build_test_server())
        with patch.object(
            generate_module, "resolve_server_spec", return_value=transport
        ):
            with pytest.raises(SystemExit):
                await generate_cli_command("weather", str(output))
        assert not output.exists()
