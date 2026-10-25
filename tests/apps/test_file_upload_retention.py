"""Optional capacity and expiry for the default upload store."""

import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import Mock

import pytest

from fastmcp import FastMCP
from fastmcp.apps import file_upload
from fastmcp.apps.file_upload import FileUpload
from fastmcp.server.context import Context
from fastmcp.server.providers.addressing import hashed_backend_name
from tests.apps.test_file_upload import _make_file


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr(file_upload, "monotonic", lambda: now[0])
    return now


def scope(name: str) -> Context:
    context = Mock(spec=Context, session_id=name)
    context._session_scope_key.return_value = name
    return context


async def test_total_size_uses_payload_sizes():
    upload = FileUpload(max_total_size=4)
    server = FastMCP("test", providers=[upload])
    first = _make_file("a.txt", "abcd")
    first["size"] = 0
    await server.call_tool(
        hashed_backend_name("Files", "store_files"), {"files": [first]}
    )

    with pytest.raises(Exception, match="total storage size"):
        await server.call_tool(
            hashed_backend_name("Files", "store_files"),
            {"files": [_make_file("b.txt", "b")]},
        )

    assert len(upload._store["__default__"]) == 1


def test_total_size_spans_scopes():
    upload = FileUpload(max_total_size=4)
    a, b = scope("a"), scope("b")
    upload.on_store([_make_file("a.txt", "abc")], a)

    with pytest.raises(ValueError, match="total storage size"):
        upload.on_store([_make_file("b.txt", "ab")], b)
    upload.on_store([_make_file("b.txt", "b")], b)

    assert [f["name"] for f in upload.on_list(a)] == ["a.txt"]
    assert [f["name"] for f in upload.on_list(b)] == ["b.txt"]


def test_rejected_batch_keeps_existing_contents():
    upload = FileUpload(max_total_size=4)
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "a")], ctx)

    with pytest.raises(ValueError, match="total storage size"):
        upload.on_store([_make_file("a.txt", "abc"), _make_file("b.txt", "bb")], ctx)

    assert upload.on_read("a.txt", ctx)["content"] == "a"
    assert [f["name"] for f in upload.on_list(ctx)] == ["a.txt"]


def test_replacements_return_capacity():
    upload = FileUpload(max_total_size=4)
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "abcd")], ctx)
    upload.on_store([_make_file("a.txt", "a")], ctx)
    upload.on_store([_make_file("b.txt", "bcd")], ctx)

    with pytest.raises(ValueError, match="total storage size"):
        upload.on_store([_make_file("a.txt", "abcd")], ctx)
    assert upload.on_read("a.txt", ctx)["content"] == "a"
    assert upload.on_read("b.txt", ctx)["content"] == "bcd"


def test_repeated_names_use_final_contents():
    upload = FileUpload(max_total_size=1)
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "abcd"), _make_file("a.txt", "a")], ctx)

    assert upload.on_read("a.txt", ctx)["content"] == "a"


def test_zero_capacity_accepts_empty_files():
    upload = FileUpload(max_total_size=0)
    ctx = scope("a")
    upload.on_store([_make_file("empty.txt", "")], ctx)

    assert upload.on_read("empty.txt", ctx)["content"] == ""
    with pytest.raises(ValueError, match="total storage size"):
        upload.on_store([_make_file("a.txt", "a")], ctx)


def test_default_storage_persists_across_calls(clock: list[float]):
    upload = FileUpload()
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "abc")], ctx)
    clock[0] += 10000
    upload.on_store([_make_file("b.txt", "bcd")], ctx)

    assert [f["name"] for f in upload.on_list(ctx)] == ["a.txt", "b.txt"]
    assert upload.on_read("a.txt", ctx)["content"] == "abc"


def test_expiry_does_not_refresh_on_read(clock: list[float]):
    upload = FileUpload(file_ttl_seconds=5)
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "abc")], ctx)
    clock[0] = 104.9
    assert upload.on_read("a.txt", ctx)["content"] == "abc"
    clock[0] = 105

    with pytest.raises(ValueError, match="not found"):
        upload.on_read("a.txt", ctx)
    assert upload.on_list(ctx) == []
    assert upload._store == {}


def test_replacement_starts_new_expiry(clock: list[float]):
    upload = FileUpload(file_ttl_seconds=5)
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "abc")], ctx)
    clock[0] = 104
    upload.on_store([_make_file("a.txt", "def")], ctx)
    clock[0] = 105
    assert upload.on_read("a.txt", ctx)["content"] == "def"
    clock[0] = 109
    assert upload.on_list(ctx) == []


def test_expiry_returns_global_capacity(clock: list[float]):
    upload = FileUpload(max_total_size=4, file_ttl_seconds=5)
    a, b = scope("a"), scope("b")
    upload.on_store([_make_file("a.txt", "abcd")], a)
    clock[0] = 105
    upload.on_store([_make_file("b.txt", "abcd")], b)

    assert upload.on_list(a) == []
    assert upload.on_read("b.txt", b)["content"] == "abcd"
    assert set(upload._store) == {"b"}


def test_list_prunes_expired_files_in_other_scopes(clock: list[float]):
    upload = FileUpload(file_ttl_seconds=5)
    a, b = scope("a"), scope("b")
    upload.on_store([_make_file("a.txt", "a")], a)
    clock[0] = 104
    upload.on_store([_make_file("b.txt", "b")], b)
    clock[0] = 105

    assert [f["name"] for f in upload.on_list(b)] == ["b.txt"]
    assert set(upload._store) == {"b"}


@pytest.mark.parametrize("total_size", [-1, -1024])
def test_total_size_requires_nonnegative_value(total_size: int):
    with pytest.raises(ValueError, match="max_total_size"):
        FileUpload(max_total_size=total_size)


@pytest.mark.parametrize("ttl", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_expiry_requires_positive_finite_value(ttl: float):
    with pytest.raises(ValueError, match="file_ttl_seconds"):
        FileUpload(file_ttl_seconds=ttl)


def test_custom_storage_controls_its_own_retention(clock: list[float]):
    stored: dict[str, dict[str, Any]] = {}

    class CustomUpload(FileUpload):
        def on_store(self, files: list[dict[str, Any]], ctx: Context):
            stored.update({f["name"]: f for f in files})
            return self.on_list(ctx)

        def on_list(self, ctx: Context):
            return [{"name": name} for name in stored]

        def on_read(self, name: str, ctx: Context):
            return stored[name]

    upload = CustomUpload(max_total_size=0, file_ttl_seconds=1)
    ctx = scope("a")
    upload.on_store([_make_file("a.txt", "abc")], ctx)
    clock[0] += 10000

    assert upload.on_list(ctx) == [{"name": "a.txt"}]
    assert base64.b64decode(upload.on_read("a.txt", ctx)["data"]) == b"abc"
    assert upload._store == {}


def test_concurrent_batches_share_capacity():
    upload = FileUpload(max_total_size=4)

    def store(name: str) -> bool:
        try:
            upload.on_store([_make_file("file.txt", "abcd")], scope(name))
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(store, ["a", "b"]))

    assert sorted(results) == [False, True]
    assert sum(len(files) for files in upload._store.values()) == 1
