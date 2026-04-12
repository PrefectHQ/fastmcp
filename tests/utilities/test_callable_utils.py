"""Tests for callable utility functions."""

import functools

from fastmcp.utilities.callable_utils import (
    get_callable_name,
    is_callable_object,
    prepare_callable,
)


class TestIsCallableObject:
    def test_function(self):
        def fn():
            pass

        assert is_callable_object(fn) is True

    def test_async_function(self):
        async def fn():
            pass

        assert is_callable_object(fn) is True

    def test_partial(self):
        def fn(x, y):
            return x + y

        assert is_callable_object(functools.partial(fn, y=1)) is True

    def test_callable_class(self):
        class MyCallable:
            def __call__(self):
                pass

        assert is_callable_object(MyCallable()) is False

    def test_string(self):
        assert is_callable_object("not a callable") is False

    def test_none(self):
        assert is_callable_object(None) is False


class TestGetCallableName:
    def test_function(self):
        def my_function():
            pass

        assert get_callable_name(my_function) == "my_function"

    def test_lambda(self):
        assert get_callable_name(lambda: None) == "<lambda>"

    def test_partial_with_update_wrapper(self):
        def add(x, y):
            return x + y

        p = functools.partial(add, y=10)
        functools.update_wrapper(p, add)
        assert get_callable_name(p) == "add"

    def test_partial_without_update_wrapper(self):
        def add(x, y):
            return x + y

        p = functools.partial(add, y=10)
        assert get_callable_name(p) == "add"

    def test_callable_class(self):
        class MyTool:
            def __call__(self):
                pass

        assert get_callable_name(MyTool()) == "MyTool"


class TestPrepareCallable:
    def test_regular_function_unchanged(self):
        def fn(x):
            return x

        assert prepare_callable(fn) is fn

    def test_strips_wrapped_from_partial(self):
        def add(x, y):
            return x + y

        p = functools.partial(add, y=10)
        functools.update_wrapper(p, add)
        assert hasattr(p, "__wrapped__")

        prepared = prepare_callable(p)
        assert isinstance(prepared, functools.partial)
        assert not hasattr(prepared, "__wrapped__")
        assert prepared.keywords == {"y": 10}

    def test_partial_without_wrapper_unchanged(self):
        def add(x, y):
            return x + y

        p = functools.partial(add, y=10)
        prepared = prepare_callable(p)
        assert isinstance(prepared, functools.partial)
        assert prepared.func is add

    def test_callable_class_unwrapped(self):
        class MyCallable:
            def __call__(self, x):
                return x

        obj = MyCallable()
        prepared = prepare_callable(obj)
        assert prepared == obj.__call__

    def test_staticmethod_unwrapped(self):
        def fn(x):
            return x

        sm = staticmethod(fn)
        assert prepare_callable(sm) is fn
