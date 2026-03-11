from __future__ import annotations

import argparse

import pytest

from purecipher.cli import build_parser, build_registry_from_args
from purecipher.registry import PureCipherRegistry


class TestPureCipherCLI:
    def test_parser_reads_basic_args(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "--signing-secret",
                "test-secret",
                "--port",
                "8100",
                "--database-path",
                "/tmp/purecipher.db",
                "--minimum-certification",
                "strict",
            ]
        )

        assert isinstance(args, argparse.Namespace)
        assert args.signing_secret == "test-secret"
        assert args.port == 8100
        assert args.database_path == "/tmp/purecipher.db"
        assert args.minimum_certification == "strict"

    def test_parser_reads_require_moderation_from_env(self, monkeypatch):
        monkeypatch.setenv("PURECIPHER_REQUIRE_MODERATION", "true")
        parser = build_parser()

        args = parser.parse_args(["--signing-secret", "test-secret"])

        assert args.require_moderation is True

    def test_parser_reads_enable_auth_from_env(self, monkeypatch):
        monkeypatch.setenv("PURECIPHER_ENABLE_AUTH", "true")
        parser = build_parser()

        args = parser.parse_args(["--signing-secret", "test-secret"])

        assert args.enable_auth is True

    def test_build_registry_from_args(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--name",
                "pc-registry",
                "--signing-secret",
                "test-secret",
                "--registry-prefix",
                "/launchpad",
            ]
        )

        registry = build_registry_from_args(args)

        assert isinstance(registry, PureCipherRegistry)
        assert registry.name == "pc-registry"
        paths = {route.path for route in registry._additional_http_routes}
        assert "/launchpad" in paths
        assert "/launchpad/tools" in paths

    def test_build_registry_from_args_enables_moderation(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--signing-secret",
                "test-secret",
                "--require-moderation",
            ]
        )

        registry = build_registry_from_args(args)

        assert isinstance(registry, PureCipherRegistry)
        assert registry.get_registry_health()["require_moderation"] is True

    def test_build_registry_from_args_enables_auth(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--signing-secret",
                "test-secret",
                "--enable-auth",
                "--jwt-secret",
                "jwt-secret",
            ]
        )

        registry = build_registry_from_args(args)

        assert isinstance(registry, PureCipherRegistry)
        assert registry.get_registry_health()["auth_enabled"] is True

    def test_build_registry_requires_secret(self):
        parser = build_parser()
        args = parser.parse_args([])
        args.signing_secret = None

        with pytest.raises(ValueError, match="signing secret"):
            build_registry_from_args(args)
