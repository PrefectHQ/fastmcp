from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmcp.cli.deploy.configuration import (
    ConfigurationStore,
    HorizonConfiguration,
)
from fastmcp.cli.deploy.credentials import CredentialStore
from fastmcp.cli.deploy.horizon_client import DEFAULT_HORIZON_API_ORIGIN
from fastmcp.cli.deploy.state import StateFileError


def test_configuration_defaults_to_the_production_origin(tmp_path: Path) -> None:
    store = ConfigurationStore(tmp_path)

    configuration = store.load()

    assert configuration.api_origin == DEFAULT_HORIZON_API_ORIGIN
    assert store.path.exists() is False


def test_configuration_stores_only_schema_and_api_origin(tmp_path: Path) -> None:
    store = ConfigurationStore(tmp_path)
    configuration = HorizonConfiguration(
        schemaVersion=1,
        apiOrigin="https://example.com/",
    )

    store.save(configuration)

    assert json.loads(store.path.read_text()) == {
        "schemaVersion": 1,
        "apiOrigin": "https://example.com",
    }
    assert store.load() == configuration


def test_configuration_rejects_organization_state(tmp_path: Path) -> None:
    store = ConfigurationStore(tmp_path)
    store.path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "apiOrigin": DEFAULT_HORIZON_API_ORIGIN,
                "currentOrganizationId": "org-id",
            }
        )
    )

    with pytest.raises(StateFileError):
        store.load()


def test_origin_change_clears_credentials_before_writing_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = CredentialStore(tmp_path)
    credentials.save("fmcp_secret")
    configuration = ConfigurationStore(tmp_path)
    configuration.save(
        HorizonConfiguration(
            schemaVersion=1,
            apiOrigin=DEFAULT_HORIZON_API_ORIGIN,
        )
    )
    events: list[str] = []
    original_clear = credentials.clear
    original_save = configuration.save

    def clear() -> None:
        events.append("clear")
        original_clear()

    def save(value: HorizonConfiguration) -> None:
        events.append("save")
        original_save(value)

    monkeypatch.setattr(credentials, "clear", clear)
    monkeypatch.setattr(configuration, "save", save)

    result = configuration.set_api_origin(
        "https://dev.horizon.prefect.io",
        credentials=credentials,
    )

    assert events == ["clear", "save"]
    assert credentials.load() is None
    assert result.api_origin == "https://dev.horizon.prefect.io"


def test_same_origin_does_not_clear_credentials(tmp_path: Path) -> None:
    credentials = CredentialStore(tmp_path)
    credentials.save("fmcp_secret")
    configuration = ConfigurationStore(tmp_path)

    configuration.set_api_origin(
        DEFAULT_HORIZON_API_ORIGIN,
        credentials=credentials,
    )

    stored = credentials.load()
    assert stored is not None
    assert stored.get_secret_value() == "fmcp_secret"


def test_failed_origin_write_leaves_no_cross_origin_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = CredentialStore(tmp_path)
    credentials.save("fmcp_secret")
    configuration = ConfigurationStore(tmp_path)

    def fail_save(value: HorizonConfiguration) -> None:
        raise StateFileError("write failed")

    monkeypatch.setattr(configuration, "save", fail_save)

    with pytest.raises(StateFileError):
        configuration.set_api_origin(
            "https://dev.horizon.prefect.io",
            credentials=credentials,
        )

    assert credentials.load() is None
    assert configuration.load().api_origin == DEFAULT_HORIZON_API_ORIGIN
