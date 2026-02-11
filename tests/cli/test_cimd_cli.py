"""Tests for the CIMD CLI commands (create and validate)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import AnyHttpUrl

from fastmcp.cli.cimd import create_command, validate_command
from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetchError, CIMDValidationError


class TestCIMDCreateCommand:
    """Tests for `fastmcp auth cimd create`."""

    def test_minimal_output(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
        )
        doc = json.loads(capsys.readouterr().out)
        assert doc["client_name"] == "Test App"
        assert doc["redirect_uris"] == ["http://localhost:*/callback"]
        assert doc["token_endpoint_auth_method"] == "none"
        assert doc["grant_types"] == ["authorization_code"]
        assert doc["response_types"] == ["code"]
        # Placeholder client_id
        assert "YOUR-DOMAIN" in doc["client_id"]

    def test_with_client_id(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            client_id="https://myapp.example.com/client.json",
        )
        doc = json.loads(capsys.readouterr().out)
        assert doc["client_id"] == "https://myapp.example.com/client.json"

    def test_with_output_file(self, tmp_path):
        output_file = tmp_path / "client.json"
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            client_id="https://example.com/client.json",
            output=str(output_file),
        )
        doc = json.loads(output_file.read_text())
        assert doc["client_id"] == "https://example.com/client.json"
        assert doc["client_name"] == "Test App"

    def test_relative_path_resolved(self, tmp_path, monkeypatch):
        """Relative paths should be resolved against cwd."""
        monkeypatch.chdir(tmp_path)
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            output="./subdir/client.json",
        )
        resolved = tmp_path / "subdir" / "client.json"
        assert resolved.exists()
        doc = json.loads(resolved.read_text())
        assert doc["client_name"] == "Test App"

    def test_with_scope(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            scope="read write",
        )
        doc = json.loads(capsys.readouterr().out)
        assert doc["scope"] == "read write"

    def test_with_client_uri(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            client_uri="https://example.com",
        )
        doc = json.loads(capsys.readouterr().out)
        assert doc["client_uri"] == "https://example.com"

    def test_with_logo_uri(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            logo_uri="https://example.com/logo.png",
        )
        doc = json.loads(capsys.readouterr().out)
        assert doc["logo_uri"] == "https://example.com/logo.png"

    def test_multiple_redirect_uris(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=[
                "http://localhost:*/callback",
                "https://myapp.example.com/callback",
            ],
        )
        doc = json.loads(capsys.readouterr().out)
        assert len(doc["redirect_uris"]) == 2

    def test_no_pretty(self, capsys: pytest.CaptureFixture[str]):
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            pretty=False,
        )
        output = capsys.readouterr().out.strip()
        # Compact JSON has no newlines within the object
        assert "\n" not in output
        doc = json.loads(output)
        assert doc["client_name"] == "Test App"

    def test_placeholder_warning_on_stderr(self, capsys: pytest.CaptureFixture[str]):
        """When outputting to stdout with no --client-id, warning goes to stderr."""
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
        )
        captured = capsys.readouterr()
        # stdout has valid JSON
        json.loads(captured.out)
        # stderr has the warning (Rich Console writes to stderr)
        assert "placeholder" in captured.err

    def test_no_warning_with_client_id(self, capsys: pytest.CaptureFixture[str]):
        """No placeholder warning when --client-id is provided."""
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
            client_id="https://example.com/client.json",
        )
        captured = capsys.readouterr()
        assert "placeholder" not in captured.err

    def test_optional_fields_omitted_when_none(
        self, capsys: pytest.CaptureFixture[str]
    ):
        """Optional fields like scope, client_uri, logo_uri are omitted if not given."""
        create_command(
            name="Test App",
            redirect_uri=["http://localhost:*/callback"],
        )
        doc = json.loads(capsys.readouterr().out)
        assert "scope" not in doc
        assert "client_uri" not in doc
        assert "logo_uri" not in doc


class TestCIMDValidateCommand:
    """Tests for `fastmcp auth cimd validate`."""

    def test_invalid_url_format(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit, match="1"):
            validate_command("http://insecure.com/client.json")
        captured = capsys.readouterr()
        assert "Invalid CIMD URL" in captured.out

    def test_root_path_rejected(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit, match="1"):
            validate_command("https://example.com/")
        captured = capsys.readouterr()
        assert "Invalid CIMD URL" in captured.out

    def test_success(self, capsys: pytest.CaptureFixture[str]):
        mock_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://myapp.example.com/client.json"),
            client_name="Test App",
            redirect_uris=["http://localhost:*/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code"],
            response_types=["code"],
        )
        with patch.object(CIMDDocument, "__init__", return_value=None):
            pass
        mock_fetch = AsyncMock(return_value=mock_doc)
        with patch(
            "fastmcp.cli.cimd.CIMDFetcher.fetch",
            mock_fetch,
        ):
            validate_command("https://myapp.example.com/client.json")
        captured = capsys.readouterr()
        assert "Valid CIMD document" in captured.out
        assert "Test App" in captured.out

    def test_fetch_error(self, capsys: pytest.CaptureFixture[str]):
        mock_fetch = AsyncMock(side_effect=CIMDFetchError("Connection refused"))
        with patch(
            "fastmcp.cli.cimd.CIMDFetcher.fetch",
            mock_fetch,
        ):
            with pytest.raises(SystemExit, match="1"):
                validate_command("https://myapp.example.com/client.json")
        captured = capsys.readouterr()
        assert "Failed to fetch" in captured.out

    def test_validation_error(self, capsys: pytest.CaptureFixture[str]):
        mock_fetch = AsyncMock(side_effect=CIMDValidationError("client_id mismatch"))
        with patch(
            "fastmcp.cli.cimd.CIMDFetcher.fetch",
            mock_fetch,
        ):
            with pytest.raises(SystemExit, match="1"):
                validate_command("https://myapp.example.com/client.json")
        captured = capsys.readouterr()
        assert "Validation error" in captured.out
