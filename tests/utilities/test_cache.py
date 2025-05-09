"""Tests for the cache.py module."""

import datetime
import time
from unittest.mock import patch

from fastmcp.utilities.cache import TimedCache


class TestTimedCache:
    """Tests for the TimedCache class."""

    def test_init(self):
        """Test that a TimedCache can be initialized with an expiration."""
        expiration = datetime.timedelta(seconds=10)
        cache = TimedCache(expiration)
        assert cache.expiration == expiration
        assert isinstance(cache.cache, dict)
        assert len(cache.cache) == 0

    def test_set(self):
        """Test that values can be set in the cache."""
        cache = TimedCache(datetime.timedelta(seconds=10))
        key, value = "test_key", "test_value"

        with patch("datetime.datetime") as mock_datetime:
            now = datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc)
            mock_datetime.now.return_value = now

            cache.set(key, value)

            # Check that the value is stored with the correct expiration
            assert key in cache.cache
            stored_value, expiration = cache.cache[key]
            assert stored_value == value
            assert expiration == now + datetime.timedelta(seconds=10)

    def test_get_found(self):
        """Test retrieving a value that exists and has not expired."""
        cache = TimedCache(datetime.timedelta(seconds=10))
        key, value = "test_key", "test_value"

        # Set a future expiration time
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=30
        )
        cache.cache[key] = (value, future)

        # The value should be returned
        assert cache.get(key) == value

    def test_get_expired(self):
        """Test retrieving a value that exists but has expired."""
        cache = TimedCache(datetime.timedelta(seconds=10))
        key, value = "test_key", "test_value"

        # Set a past expiration time
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=1
        )
        cache.cache[key] = (value, past)

        # Should return NOT_FOUND
        assert cache.get(key) is TimedCache.NOT_FOUND

    def test_get_not_found(self):
        """Test retrieving a value that doesn't exist in the cache."""
        cache = TimedCache(datetime.timedelta(seconds=10))

        # Key doesn't exist
        assert cache.get("nonexistent_key") is TimedCache.NOT_FOUND

    def test_clear(self):
        """Test that the cache can be cleared."""
        cache = TimedCache(datetime.timedelta(seconds=10))

        # Add some items
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        assert len(cache.cache) == 2

        # Clear the cache
        cache.clear()
        assert len(cache.cache) == 0

    def test_real_expiration(self):
        """Test that values actually expire after the specified time."""
        # Use a very short expiration for the test
        cache = TimedCache(datetime.timedelta(milliseconds=50))
        key, value = "test_key", "test_value"

        cache.set(key, value)
        # Value should be available immediately
        assert cache.get(key) == value

        # Wait for expiration
        time.sleep(0.06)  # 60 milliseconds, slightly longer than expiration

        # Value should now be expired
        assert cache.get(key) is TimedCache.NOT_FOUND

    def test_overwrite_value(self):
        """Test that setting a key that already exists overwrites the old value."""
        cache = TimedCache(datetime.timedelta(seconds=10))
        key = "test_key"

        # Set initial value
        cache.set(key, "initial_value")
        assert cache.get(key) == "initial_value"

        # Overwrite with new value
        cache.set(key, "new_value")
        assert cache.get(key) == "new_value"

    def test_extends_expiration_on_overwrite(self):
        """Test that overwriting a key extends its expiration time."""
        cache = TimedCache(datetime.timedelta(seconds=10))
        key = "test_key"

        with patch("datetime.datetime") as mock_datetime:
            # Set initial value at t=0
            initial_time = datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc)
            mock_datetime.now.return_value = initial_time
            cache.set(key, "initial_value")

            initial_expiration = cache.cache[key][1]
            assert initial_expiration == initial_time + datetime.timedelta(seconds=10)

            # Overwrite at t=5
            later_time = initial_time + datetime.timedelta(seconds=5)
            mock_datetime.now.return_value = later_time
            cache.set(key, "new_value")

            # Expiration should be extended
            new_expiration = cache.cache[key][1]
            assert new_expiration == later_time + datetime.timedelta(seconds=10)

    def test_different_key_types(self):
        """Test that different types of keys can be used."""
        cache = TimedCache(datetime.timedelta(seconds=10))

        # Test various key types
        keys_and_values = [
            (42, "int_value"),
            (3.14, "float_value"),
            ((1, 2), "tuple_value"),
            (frozenset({1, 2, 3}), "frozenset_value"),
        ]

        for key, value in keys_and_values:
            cache.set(key, value)
            assert cache.get(key) == value

    def test_none_value(self):
        """Test that None can be stored as a value."""
        cache = TimedCache(datetime.timedelta(seconds=10))
        key = "none_key"

        cache.set(key, None)
        # The stored value is None, but get() should return None, not NOT_FOUND
        assert cache.get(key) is None

    def test_edge_case_zero_expiration(self):
        """Test with a zero expiration time."""
        cache = TimedCache(datetime.timedelta(seconds=0))
        key, value = "test_key", "test_value"

        cache.set(key, value)
        # The value might already be expired by the time we call get()
        # We can't make strong assertions here due to timing variability
        retrieved = cache.get(key)
        assert retrieved in (value, TimedCache.NOT_FOUND)

    def test_negative_expiration(self):
        """Test with a negative expiration time."""
        cache = TimedCache(datetime.timedelta(seconds=-1))
        key, value = "test_key", "test_value"

        cache.set(key, value)
        # Value should be immediately expired
        assert cache.get(key) is TimedCache.NOT_FOUND

    def test_cache_consistency(self):
        """Test cache consistency with multiple operations."""
        cache = TimedCache(datetime.timedelta(seconds=10))

        # Add multiple items
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        # Check all items
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"

        # Overwrite one item
        cache.set("key2", "updated_value")

        # Check again
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "updated_value"
        assert cache.get("key3") == "value3"

        # Clear and verify all items are gone
        cache.clear()
        assert cache.get("key1") is TimedCache.NOT_FOUND
        assert cache.get("key2") is TimedCache.NOT_FOUND
        assert cache.get("key3") is TimedCache.NOT_FOUND

    def test_large_expiration(self):
        """Test with a very large expiration time."""
        # One year expiration
        cache = TimedCache(datetime.timedelta(days=365))
        key, value = "test_key", "test_value"

        cache.set(key, value)
        assert cache.get(key) == value

    def test_many_items(self):
        """Test cache with many items."""
        cache = TimedCache(datetime.timedelta(seconds=10))

        # Add 1000 items
        for i in range(1000):
            cache.set(f"key{i}", f"value{i}")

        # Check size
        assert len(cache.cache) == 1000

        # Check some random items
        for i in [0, 123, 456, 789, 999]:
            assert cache.get(f"key{i}") == f"value{i}"
