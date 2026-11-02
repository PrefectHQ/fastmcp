"""Tests for timeout normalization utilities."""

import datetime
from typing import Any, cast

import pytest

from fastmcp.utilities.timeout import (
    normalize_timeout_to_seconds,
    normalize_timeout_to_timedelta,
)


class TestNormalizeTimeoutToSeconds:
    def test_none_stays_none(self):
        assert normalize_timeout_to_seconds(None) is None

    def test_numeric_values_become_float_seconds(self):
        assert normalize_timeout_to_seconds(3) == 3.0
        assert normalize_timeout_to_seconds(2.5) == 2.5

    def test_zero_values_disable_timeout(self):
        assert normalize_timeout_to_seconds(0) is None
        assert normalize_timeout_to_seconds(datetime.timedelta(seconds=0)) is None

    def test_timedelta_becomes_seconds(self):
        assert (
            normalize_timeout_to_seconds(datetime.timedelta(milliseconds=250)) == 0.25
        )

    def test_invalid_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Invalid timeout type"):
            normalize_timeout_to_seconds(cast(Any, "1"))

    def test_non_none_return_invariants(self):
        res_int = normalize_timeout_to_seconds(5)
        assert res_int is not None
        assert res_int == 5.0
        assert isinstance(res_int, float)
        res_td = normalize_timeout_to_seconds(datetime.timedelta(seconds=12))
        assert res_td is not None
        assert res_td == 12.0
        assert isinstance(res_td, float)
        assert normalize_timeout_to_seconds(0) is None
        assert normalize_timeout_to_seconds(None) is None
        assert normalize_timeout_to_seconds(datetime.timedelta(seconds=0)) is None



class TestNormalizeTimeoutToTimedelta:
    def test_none_stays_none(self):
        assert normalize_timeout_to_timedelta(None) is None

    def test_timedelta_is_returned_unchanged(self):
        timeout = datetime.timedelta(seconds=5)
        assert normalize_timeout_to_timedelta(timeout) is timeout

    def test_numeric_values_become_timedeltas(self):
        assert normalize_timeout_to_timedelta(3) == datetime.timedelta(seconds=3)
        assert normalize_timeout_to_timedelta(0.5) == datetime.timedelta(seconds=0.5)

    def test_invalid_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Invalid timeout type"):
            normalize_timeout_to_timedelta(cast(Any, "1"))
