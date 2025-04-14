import functools
from collections.abc import Callable
from typing import Any

import pytest

from fastmcp.utilities.decorators import DecoratedFunction

DECORATOR_CALLED = []


def decorator(fn: Callable[..., Any]) -> DecoratedFunction[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        DECORATOR_CALLED.append((args, kwargs))
        return fn(*args, **kwargs)

    return DecoratedFunction(wrapper)


@pytest.fixture(autouse=True)
def reset_decorator_called():
    DECORATOR_CALLED.clear()
    yield
    DECORATOR_CALLED.clear()


@decorator
def add(a: int, b: int) -> int:
    return a + b


@decorator
async def add_async(a: int, b: int) -> int:
    return a + b


class DecoratedClass:
    def __init__(self, x: int):
        self.x = x

    @decorator
    def add(self, a: int, b: int) -> int:
        return a + b + self.x

    @decorator
    async def add_async(self, a: int, b: int) -> int:
        return a + b + self.x

    @classmethod
    @decorator
    def add_classmethod(cls, a: int, b: int) -> int:
        return a + b

    @staticmethod
    @decorator
    def add_staticmethod(a: int, b: int) -> int:
        return a + b

    @classmethod
    @decorator
    async def add_classmethod_async(cls, a: int, b: int) -> int:
        return a + b

    @staticmethod
    @decorator
    async def add_staticmethod_async(a: int, b: int) -> int:
        return a + b

    @decorator
    @classmethod
    def add_classmethod_reverse_decorator_order(cls, a: int, b: int) -> int:
        return a + b

    @decorator
    @staticmethod
    def add_staticmethod_reverse_decorator_order(a: int, b: int) -> int:
        return a + b

    @decorator
    @classmethod
    async def add_classmethod_async_reverse_decorator_order(cls, a: int, b: int) -> int:
        return a + b

    @decorator
    @staticmethod
    async def add_staticmethod_async_reverse_decorator_order(a: int, b: int) -> int:
        return a + b


def test_add():
    assert add(1, 2) == 3
    assert DECORATOR_CALLED == [((1, 2), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert add(a=3, b=4) == 7
    assert DECORATOR_CALLED == [((), {"a": 3, "b": 4})]


async def test_add_async():
    assert await add_async(1, 2) == 3
    assert DECORATOR_CALLED == [((1, 2), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert await add_async(a=3, b=4) == 7
    assert DECORATOR_CALLED == [((), {"a": 3, "b": 4})]


def test_instance_method():
    obj = DecoratedClass(10)
    assert obj.add(2, 3) == 15
    assert DECORATOR_CALLED == [((obj, 2, 3), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert obj.add(a=4, b=5) == 19
    assert DECORATOR_CALLED == [((obj,), {"a": 4, "b": 5})]


async def test_instance_method_async():
    obj = DecoratedClass(10)
    assert await obj.add_async(2, 3) == 15
    assert DECORATOR_CALLED == [((obj, 2, 3), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert await obj.add_async(a=4, b=5) == 19
    assert DECORATOR_CALLED == [((obj,), {"a": 4, "b": 5})]


def test_classmethod():
    assert DecoratedClass.add_classmethod(1, 2) == 3
    assert DECORATOR_CALLED == [((DecoratedClass, 1, 2), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert DecoratedClass.add_classmethod(a=3, b=4) == 7
    assert DECORATOR_CALLED == [((DecoratedClass,), {"a": 3, "b": 4})]
    DECORATOR_CALLED.clear()

    # Test via instance
    obj = DecoratedClass(10)
    assert obj.add_classmethod(5, 6) == 11
    assert DECORATOR_CALLED == [((DecoratedClass, 5, 6), {})]


async def test_classmethod_async():
    assert await DecoratedClass.add_classmethod_async(1, 2) == 3
    assert DECORATOR_CALLED == [((DecoratedClass, 1, 2), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert await DecoratedClass.add_classmethod_async(a=3, b=4) == 7
    assert DECORATOR_CALLED == [((DecoratedClass,), {"a": 3, "b": 4})]
    DECORATOR_CALLED.clear()

    # Test via instance
    obj = DecoratedClass(10)
    assert await obj.add_classmethod_async(5, 6) == 11
    assert DECORATOR_CALLED == [((DecoratedClass, 5, 6), {})]


def test_classmethod_wrong_order():
    with pytest.raises(
        TypeError,
        match="To apply this decorator to a classmethod, apply the decorator first, then @classmethod on top.",
    ):
        DecoratedClass.add_classmethod_reverse_decorator_order(1, 2)


async def test_classmethod_async_wrong_order():
    with pytest.raises(
        TypeError,
        match="To apply this decorator to a classmethod, apply the decorator first, then @classmethod on top.",
    ):
        await DecoratedClass.add_classmethod_async_reverse_decorator_order(1, 2)


def test_staticmethod():
    assert DecoratedClass.add_staticmethod(1, 2) == 3
    assert DECORATOR_CALLED == [((1, 2), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert DecoratedClass.add_staticmethod(a=3, b=4) == 7
    assert DECORATOR_CALLED == [((), {"a": 3, "b": 4})]
    DECORATOR_CALLED.clear()

    # Test via instance
    obj = DecoratedClass(10)
    assert obj.add_staticmethod(5, 6) == 11
    assert DECORATOR_CALLED == [((5, 6), {})]


async def test_staticmethod_async():
    assert await DecoratedClass.add_staticmethod_async(1, 2) == 3
    assert DECORATOR_CALLED == [((1, 2), {})]
    DECORATOR_CALLED.clear()

    # Test with keyword arguments
    assert await DecoratedClass.add_staticmethod_async(a=3, b=4) == 7
    assert DECORATOR_CALLED == [((), {"a": 3, "b": 4})]
    DECORATOR_CALLED.clear()

    # Test via instance
    obj = DecoratedClass(10)
    assert await obj.add_staticmethod_async(5, 6) == 11
    assert DECORATOR_CALLED == [((5, 6), {})]


def test_staticmethod_wrong_order():
    assert DecoratedClass.add_staticmethod_reverse_decorator_order(1, 2) == 3
    assert DECORATOR_CALLED == [((1, 2), {})]


async def test_staticmethod_async_wrong_order():
    assert (
        await DecoratedClass.add_staticmethod_async_reverse_decorator_order(1, 2) == 3
    )
    assert DECORATOR_CALLED == [((1, 2), {})]
