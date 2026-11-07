"""Exercise real NumPy scalars when the optional dependency is installed."""

import math

import pytest

from fastmcp.exceptions import ToolError
from fastmcp.tools.base import default_serializer

numpy = pytest.importorskip("numpy")


@pytest.mark.parametrize("dtype", ["float32", "float64", "longdouble"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_numpy_nonfinite_values_are_rejected(dtype: str, value: float) -> None:
    scalar = getattr(numpy, dtype)(value)
    with pytest.raises(ToolError, match="non-finite"):
        default_serializer({"mean": scalar})


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [("float32", '"2.5"'), ("float64", "2.5"), ("longdouble", '"2.5"')],
)
def test_numpy_finite_values_keep_existing_serialization(
    dtype: str, expected: str
) -> None:
    assert default_serializer(getattr(numpy, dtype)(2.5)) == expected


def test_numpy_extended_finite_range_keeps_string_encoding() -> None:
    if numpy.finfo(numpy.longdouble).max <= numpy.finfo(numpy.float64).max:
        pytest.skip("longdouble has no wider finite range on this platform")

    scalar = numpy.longdouble(numpy.finfo(numpy.float64).max) * 2
    assert numpy.isfinite(scalar)
    assert math.isinf(float(scalar))
    assert default_serializer({"mean": scalar}) == f'{{"mean":"{scalar}"}}'
