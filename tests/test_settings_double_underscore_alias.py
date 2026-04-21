"""Tests for the ``FASTMCP__<FIELD>`` alias accepted alongside the canonical
``FASTMCP_<FIELD>`` env var form.

See ``_inject_prefix_aliases`` in ``fastmcp.settings``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmcp.settings import Settings


class TestDoubleUnderscoreEnvAlias:
    """``FASTMCP__<FIELD>`` should be accepted as an alias for ``FASTMCP_<FIELD>``."""

    def test_canonical_form_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FASTMCP_HOME", "/tmp/canonical")
        assert Settings().home == Path("/tmp/canonical")

    def test_double_underscore_alias_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FASTMCP__HOME", "/tmp/alias")
        assert Settings().home == Path("/tmp/alias")

    def test_canonical_wins_over_alias_when_both_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FASTMCP_HOME", "/tmp/canonical-wins")
        monkeypatch.setenv("FASTMCP__HOME", "/tmp/alias-loses")
        assert Settings().home == Path("/tmp/canonical-wins")

    def test_nested_canonical_form_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nested-delimiter semantics (FASTMCP_<SUBMODEL>__<FIELD>) must not break.
        monkeypatch.setenv("FASTMCP_DOCKET__NAME", "nested-canonical")
        assert Settings().docket.name == "nested-canonical"

    def test_nested_via_double_underscore_prefix_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The alias only translates the namespace boundary; the nested `__`
        # delimiter between submodel and field is preserved verbatim.
        monkeypatch.setenv("FASTMCP__DOCKET__NAME", "nested-via-alias")
        assert Settings().docket.name == "nested-via-alias"

    def test_alias_does_not_affect_non_fastmcp_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sanity check: nothing outside the FASTMCP_ namespace is translated.
        monkeypatch.setenv("SOMETHING__ELSE", "untouched")
        monkeypatch.setenv("FASTMCP__HOME", "/tmp/fastmcp-only")
        settings = Settings()
        assert settings.home == Path("/tmp/fastmcp-only")

    def test_dotenv_file_alias_is_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Clear any inherited vars so only the .env file value matters.
        monkeypatch.delenv("FASTMCP__HOME", raising=False)
        monkeypatch.delenv("FASTMCP_HOME", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FASTMCP__HOME=/tmp/from-dotenv\n")
        # pydantic-settings supports overriding env_file at construction time.
        assert Settings(_env_file=str(env_file)).home == Path("/tmp/from-dotenv")  # type: ignore[call-arg]
