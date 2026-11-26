import io
import urllib.error

import pytest

from flows import agentic_github
from flows.agentic_github import GitHub


def gone(code: int):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "", {}, io.BytesIO())  # type: ignore[arg-type]

    return urlopen


@pytest.mark.parametrize("code", [404, 410])
def test_reading_a_missing_or_deleted_item_finds_nothing(monkeypatch, code) -> None:
    # 11-26: a gate-closed PR named a deleted issue; its 410 failed the whole digest.
    monkeypatch.setattr(agentic_github.urllib.request, "urlopen", gone(code))
    assert GitHub("token").request("repos/PrefectHQ/fastmcp/issues/1") is None


def test_writing_to_a_deleted_item_still_raises(monkeypatch) -> None:
    monkeypatch.setattr(agentic_github.urllib.request, "urlopen", gone(410))
    with pytest.raises(urllib.error.HTTPError):
        GitHub("token").request("repos/PrefectHQ/fastmcp/issues/1", {"state": "closed"})
