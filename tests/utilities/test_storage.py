"""Tests for KVStorage implementations."""

from pathlib import Path

import pytest

from fastmcp.utilities.storage import InMemoryStorage, JSONFileStorage


class TestJSONFileStorage:
    """Tests for file-based JSON storage."""

    @pytest.fixture
    def temp_storage(self, tmp_path: Path) -> JSONFileStorage:
        """Create a JSONFileStorage with temp directory."""
        return JSONFileStorage(tmp_path / "storage")

    async def test_basic_get_set_delete(self, temp_storage: JSONFileStorage):
        """Test basic storage operations."""
        # Initially empty
        assert await temp_storage.get("key1") is None

        # Set a value
        data = {"name": "test", "value": 123}
        await temp_storage.set("key1", data)

        # Get it back
        loaded = await temp_storage.get("key1")
        assert loaded == data

        # Delete it
        await temp_storage.delete("key1")
        assert await temp_storage.get("key1") is None

    async def test_special_characters_in_keys(self, temp_storage: JSONFileStorage):
        """Test that special characters in keys are handled safely."""
        key = "user/123:test.json?query=value"
        data = {"test": "data"}

        await temp_storage.set(key, data)
        loaded = await temp_storage.get(key)
        assert loaded == data

        # Verify the file was created with safe name
        files = list(temp_storage.cache_dir.glob("*.json"))
        assert len(files) == 1
        assert "/" not in files[0].name
        assert ":" not in files[0].name
        assert "?" not in files[0].name

    async def test_multiple_keys(self, temp_storage: JSONFileStorage):
        """Test storing multiple keys."""
        data1 = {"id": 1}
        data2 = {"id": 2}
        data3 = {"id": 3}

        await temp_storage.set("key1", data1)
        await temp_storage.set("key2", data2)
        await temp_storage.set("key3", data3)

        assert await temp_storage.get("key1") == data1
        assert await temp_storage.get("key2") == data2
        assert await temp_storage.get("key3") == data3

        # Delete one
        await temp_storage.delete("key2")
        assert await temp_storage.get("key1") == data1
        assert await temp_storage.get("key2") is None
        assert await temp_storage.get("key3") == data3

    async def test_overwrite_existing(self, temp_storage: JSONFileStorage):
        """Test overwriting existing values."""
        await temp_storage.set("key", {"version": 1})
        await temp_storage.set("key", {"version": 2})

        loaded = await temp_storage.get("key")
        assert loaded == {"version": 2}

    async def test_persistence_across_instances(self, tmp_path: Path):
        """Test that data persists across storage instances."""
        storage_dir = tmp_path / "persistent"

        # First instance
        storage1 = JSONFileStorage(storage_dir)
        data = {"persistent": True, "value": 42}
        await storage1.set("mykey", data)

        # New instance, same directory
        storage2 = JSONFileStorage(storage_dir)
        loaded = await storage2.get("mykey")
        assert loaded == data

    async def test_delete_nonexistent(self, temp_storage: JSONFileStorage):
        """Test deleting non-existent key doesn't error."""
        # Should not raise
        await temp_storage.delete("nonexistent")


class TestInMemoryStorage:
    """Tests for in-memory storage."""

    @pytest.fixture
    def memory_storage(self) -> InMemoryStorage:
        """Create an InMemoryStorage instance."""
        return InMemoryStorage()

    async def test_basic_operations(self, memory_storage: InMemoryStorage):
        """Test basic storage operations."""
        # Initially empty
        assert await memory_storage.get("key1") is None

        # Set and get
        data = {"name": "test", "value": 123}
        await memory_storage.set("key1", data)
        assert await memory_storage.get("key1") == data

        # Delete
        await memory_storage.delete("key1")
        assert await memory_storage.get("key1") is None

    async def test_no_persistence(self):
        """Test that data doesn't persist across instances."""
        storage1 = InMemoryStorage()
        await storage1.set("key", {"value": 1})

        storage2 = InMemoryStorage()
        assert await storage2.get("key") is None

    async def test_isolation_between_keys(self, memory_storage: InMemoryStorage):
        """Test that keys are isolated from each other."""
        data1 = {"id": 1, "nested": {"value": "a"}}
        data2 = {"id": 2, "nested": {"value": "b"}}

        await memory_storage.set("key1", data1)
        await memory_storage.set("key2", data2)

        # Modify retrieved data shouldn't affect stored
        retrieved = await memory_storage.get("key1")
        if retrieved:
            retrieved["modified"] = True

        # Original should be unchanged
        assert await memory_storage.get("key1") == data1
