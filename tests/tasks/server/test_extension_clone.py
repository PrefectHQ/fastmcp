from datetime import timedelta
from threading import Lock

import pytest
from redis.credentials import CredentialProvider

from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension


class LockedCredentials(CredentialProvider):
    def __init__(self) -> None:
        self.lock = Lock()
        self.password = "first"

    def get_credentials(self) -> tuple[str, str]:
        with self.lock:
            return "tasks", self.password


@pytest.mark.parametrize("with_provider", [False, True])
def test_clone_preserves_configuration_and_clears_binding(
    monkeypatch: pytest.MonkeyPatch, with_provider: bool
) -> None:
    monkeypatch.setenv("FASTMCP_DOCKET_NAME", "original-queue")
    provider = LockedCredentials() if with_provider else None
    original = TasksExtension(
        credential_provider=provider,
        url="redis://localhost:6379/1",
        concurrency=3,
        reconnection_delay=timedelta(milliseconds=20),
    )
    first = FastMCP("first")
    first.add_extension(original)
    monkeypatch.setenv("FASTMCP_DOCKET_NAME", "changed-queue")

    clone = original.clone()
    second = FastMCP("second")
    second.add_extension(clone)

    assert original.server is first
    assert clone.server is second
    assert clone.docket_settings == original.docket_settings
    assert clone.docket_settings.name == "original-queue"
    clone.docket_settings.concurrency = 7
    assert original.docket_settings.concurrency == 3
    assert clone._credential_provider is provider
    if provider is not None:
        provider.password = "second"
        assert clone._credential_provider is not None
        assert clone._credential_provider.get_credentials() == ("tasks", "second")
