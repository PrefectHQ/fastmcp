import inspect
from collections.abc import Callable
from typing import Generic, ParamSpec, TypeVar, cast, overload

from typing_extensions import Self

R = TypeVar("R")
P = ParamSpec("P")


class DecoratedFunction(Generic[P, R]):
    """Descriptor for decorated functions.

    You can return this object from a decorator to ensure that it works across
    all types of functions: vanilla, instance methods, class methods, and static
    methods; both synchronous and asynchronous.

    This class is used to store the original function and metadata about how to
    register it as a tool.

    Example usage:

    ```python
    def my_decorator(fn: Callable[P, R]) -> DecoratedFunction[P, R]:
        return DecoratedFunction(fn)
    ```

    On a function:
    ```python
    @my_decorator
    def my_function(a: int, b: int) -> int:
        return a + b
    ```

    On an instance method:
    ```python
    class Test:
        @my_decorator
        def my_function(self, a: int, b: int) -> int:
            return a + b
    ```

    On a class method:
    ```python
    class Test:
        @classmethod
        @my_decorator
        def my_function(cls, a: int, b: int) -> int:
            return a + b
    ```

    Note that for classmethods, the decorator must be applied first, then
    `@classmethod` on top.

    On a static method:
    ```python
    class Test:
        @staticmethod
        @my_decorator
        def my_function(a: int, b: int) -> int:
            return a + b
    ```
    """

    def __init__(self, fn: Callable[P, R]):
        self.fn = fn

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Call the original function."""
        try:
            return self.fn(*args, **kwargs)
        except TypeError as e:
            if "'classmethod' object is not callable" in str(e):
                raise TypeError(
                    "To apply this decorator to a classmethod, apply the decorator first, then @classmethod on top."
                )
            raise

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(
        self, instance: object, owner: type | None = None
    ) -> Callable[P, R]: ...

    def __get__(
        self, instance: object | None, owner: type | None = None
    ) -> Self | Callable[P, R]:
        """Return the original function when accessed from an instance, or self when accessed from the class."""
        if instance is None:
            return self
        # Return the original function bound to the instance
        return cast(Callable[P, R], self.fn.__get__(instance, owner))

    def __repr__(self) -> str:
        """Return a representation that matches Python's function representation."""
        module = getattr(self.fn, "__module__", "unknown")
        qualname = getattr(self.fn, "__qualname__", str(self.fn))
        sig_str = str(inspect.signature(self.fn))
        return f"<function {module}.{qualname}{sig_str}>"
