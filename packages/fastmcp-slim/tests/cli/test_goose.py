from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from fastmcp.cli.install.goose import (
    _build_uvx_command,
    _slugify,
    generate_goose_deeplink,
    goose_command,
    install_goose,
)


class TestSlugify:
    def test_simple_name(self):
        assert _slugify("My Server") == "my-server"

    def test_special_characters(self):
        assert _slugify("my_server (v2.0)") == "my-server-v2-0"

    def test_already_slugified(self):
        assert _slugify("my-server") == "my-server"

    def test_empty_string(self):
        assert _slugify("") == "fastmcp-server"

    def test_only_special_chars(self):
        assert _slugify("!!!") == "fastmcp-server"

    def test_consecutive_hyphens_collapsed(self):
        assert _slugify("a---b") == "a-b"

    def test_leading_trailing_stripped(self):
        assert _slugify("--hello--") == "hello"


class TestBuildUvxCommand:
    def test_basic(self):
        cmd = _build_uvx_command("server.py")
        assert cmd == ["uvx", "fastmcp", "run", "server.py"]

    def test_with_python_version(self):
        cmd = _build_uvx_command("server.py", python_version="3.11")
        assert cmd == [
            "uvx",
            "--python",
            "3.11",
            "fastmcp",
            "run",
            "server.py",
        ]

    def test_with_packages(self):
        cmd = _build_uvx_command("server.py", with_packages=["numpy", "pandas"])
        assert "--with" in cmd
        assert "numpy" in cmd
        assert "pandas" in cmd

    def test_fastmcp_not_in_with(self):
        cmd = _build_uvx_command("server.py", with_packages=["fastmcp", "numpy"])
        # fastmcp is the command itself, so it shouldn't appear in --with
        with_indices = [i for i, v in enumerate(cmd) if v == "--with"]
        with_values = [cmd[i + 1] for i in with_indices]
        assert "fastmcp" not in with_values

    def test_packages_sorted_and_deduplicated(self):
        cmd = _build_uvx_command(
            "server.py", with_packages=["pandas", "numpy", "pandas"]
        )
        with_indices = [i for i, v in enumerate(cmd) if v == "--with"]
        with_values = [cmd[i + 1] for i in with_indices]
        assert with_values == ["numpy", "pandas"]

    def test_server_spec_with_object(self):
        cmd = _build_uvx_command("server.py:app")
        assert cmd[-1] == "server.py:app"


class TestGooseDeeplinkGeneration:
    def test_basic_deeplink(self):
        deeplink = generate_goose_deeplink(
            name="test-server",
            command="uvx",
            args=["fastmcp", "run", "server.py"],
        )
        assert deeplink.startswith("goose://extension?")
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert params["cmd"] == ["uvx"]
        assert params["name"] == ["test-server"]
        assert params["id"] == ["test-server"]

    def test_special_characters_in_name(self):
        deeplink = generate_goose_deeplink(
            name="my server (test)",
            command="uvx",
            args=["fastmcp", "run", "server.py"],
        )
        assert "name=my%20server%20%28test%29" in deeplink
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert params["id"] == ["my-server-test"]

    def test_url_injection_protection(self):
        deeplink = generate_goose_deeplink(
            name="test&evil=true",
            command="uvx",
            args=["fastmcp", "run", "server.py"],
        )
        assert "name=test%26evil%3Dtrue" in deeplink
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert params["name"] == ["test&evil=true"]

    def test_dangerous_characters_encoded(self):
        dangerous_names = [
            ("test|calc", "test%7Ccalc"),
            ("test;calc", "test%3Bcalc"),
            ("test<calc", "test%3Ccalc"),
            ("test>calc", "test%3Ecalc"),
            ("test`calc", "test%60calc"),
            ("test$calc", "test%24calc"),
            ("test'calc", "test%27calc"),
            ('test"calc', "test%22calc"),
            ("test calc", "test%20calc"),
            ("test#anchor", "test%23anchor"),
            ("test?query=val", "test%3Fquery%3Dval"),
        ]
        for dangerous_name, expected_encoded in dangerous_names:
            deeplink = generate_goose_deeplink(
                name=dangerous_name, command="uvx", args=["fastmcp", "run", "server.py"]
            )
            assert f"name={expected_encoded}" in deeplink, (
                f"Failed to encode {dangerous_name}"
            )

    def test_custom_description(self):
        deeplink = generate_goose_deeplink(
            name="my-server",
            command="uvx",
            args=["fastmcp", "run", "server.py"],
            description="My custom MCP server",
        )
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert params["description"] == ["My custom MCP server"]

    def test_args_with_special_characters(self):
        deeplink = generate_goose_deeplink(
            name="test",
            command="uvx",
            args=[
                "--with",
                "numpy>=1.20",
                "fastmcp",
                "run",
                "server.py:MyApp",
            ],
        )
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert "numpy>=1.20" in params["arg"]
        assert "server.py:MyApp" in params["arg"]

    def test_empty_args(self):
        deeplink = generate_goose_deeplink(name="simple", command="python", args=[])
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert "arg" not in params
        assert params["cmd"] == ["python"]

    def test_command_with_path(self):
        deeplink = generate_goose_deeplink(
            name="test",
            command="/usr/local/bin/uvx",
            args=["fastmcp", "run", "server.py"],
        )
        parsed = urlparse(deeplink)
        params = parse_qs(parsed.query)
        assert params["cmd"] == ["/usr/local/bin/uvx"]


class TestInstallGoose:
    @patch("fastmcp.cli.install.goose.open_deeplink")
    @patch("fastmcp.cli.install.goose.print")
    def test_success(self, mock_print, mock_open):
        mock_open.return_value = True
        result = install_goose(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
        )
        assert result is True
        mock_open.assert_called_once()
        call_url = mock_open.call_args[0][0]
        assert call_url.startswith("goose://extension?")
        assert mock_open.call_args[1] == {"expected_scheme": "goose"}

    @patch("fastmcp.cli.install.goose.open_deeplink")
    @patch("fastmcp.cli.install.goose.print")
    def test_success_uses_uvx(self, mock_print, mock_open):
        mock_open.return_value = True
        install_goose(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
        )
        call_url = mock_open.call_args[0][0]
        parsed = urlparse(call_url)
        params = parse_qs(parsed.query)
        assert params["cmd"] == ["uvx"]
        assert "fastmcp" in params["arg"]

    @patch("fastmcp.cli.install.goose.open_deeplink")
    @patch("fastmcp.cli.install.goose.print")
    def test_failure(self, mock_print, mock_open):
        mock_open.return_value = False
        result = install_goose(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
        )
        assert result is False

    @patch("fastmcp.cli.install.goose.open_deeplink")
    @patch("fastmcp.cli.install.goose.print")
    def test_with_server_object(self, mock_print, mock_open):
        mock_open.return_value = True
        install_goose(
            file=Path("/path/to/server.py"),
            server_object="app",
            name="test-server",
        )
        call_url = mock_open.call_args[0][0]
        parsed = urlparse(call_url)
        params = parse_qs(parsed.query)
        args = params["arg"]
        assert any("server.py:app" in unquote(a) for a in args)

    @patch("fastmcp.cli.install.goose.open_deeplink")
    @patch("fastmcp.cli.install.goose.print")
    def test_with_packages(self, mock_print, mock_open):
        mock_open.return_value = True
        install_goose(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
            with_packages=["numpy", "pandas"],
        )
        call_url = mock_open.call_args[0][0]
        parsed = urlparse(call_url)
        params = parse_qs(parsed.query)
        args = params["arg"]
        assert "numpy" in args
        assert "pandas" in args

    @patch("fastmcp.cli.install.goose.open_deeplink")
    @patch("fastmcp.cli.install.goose.print")
    def test_fallback_message_on_failure(self, mock_print, mock_open):
        mock_open.return_value = False
        install_goose(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
        )
        fallback_calls = [
            call
            for call in mock_print.call_args_list
            if "copy this link" in str(call).lower() or "goose://" in str(call)
        ]
        assert len(fallback_calls) > 0


class TestGooseCommand:
    @patch("fastmcp.cli.install.goose.install_goose")
    @patch("fastmcp.cli.install.goose.process_common_args")
    async def test_basic(self, mock_process, mock_install):
        mock_process.return_value = (Path("server.py"), None, "test-server", [], {})
        mock_install.return_value = True
        await goose_command("server.py")
        mock_install.assert_called_once_with(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
            with_packages=[],
            python_version=None,
        )

    @patch("fastmcp.cli.install.goose.install_goose")
    @patch("fastmcp.cli.install.goose.process_common_args")
    async def test_failure_exits(self, mock_process, mock_install):
        mock_process.return_value = (Path("server.py"), None, "test-server", [], {})
        mock_install.return_value = False
        with pytest.raises(SystemExit) as exc_info:
            await goose_command("server.py")
        assert exc_info.value.code == 1
