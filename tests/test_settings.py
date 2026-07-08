import pytest

from fastmcp import settings
from fastmcp.settings import Settings
from fastmcp.utilities.tests import temporary_settings


def test_get_setting_reads_nested_values():
    test_settings = Settings()

    assert test_settings.get_setting("docket__name") == "fastmcp"
    assert test_settings.get_setting("docket__redelivery_timeout__seconds") == 300


def test_set_setting_updates_nested_values():
    test_settings = Settings()

    test_settings.set_setting("docket__name", "worker-queue")

    assert test_settings.docket.name == "worker-queue"
    assert test_settings.get_setting("docket__name") == "worker-queue"


def test_temporary_settings_restores_nested_values():
    original_name = settings.get_setting("docket__name")

    with temporary_settings(docket__name="temporary-queue"):
        assert settings.get_setting("docket__name") == "temporary-queue"

    assert settings.get_setting("docket__name") == original_name


def test_get_setting_raises_for_missing_nested_parent():
    test_settings = Settings()

    with pytest.raises(AttributeError) as exc_info:
        test_settings.get_setting("docket__missing__value")

    assert str(exc_info.value) == "Setting missing does not exist."


def test_http_host_origin_protection_defaults_to_false():
    assert Settings().http_host_origin_protection is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("auto", "auto"),
        ("true", True),
        ("false", False),
    ],
)
def test_http_host_origin_protection_env_var(value, expected, monkeypatch):
    monkeypatch.setenv("FASTMCP_HTTP_HOST_ORIGIN_PROTECTION", value)

    assert Settings().http_host_origin_protection == expected
