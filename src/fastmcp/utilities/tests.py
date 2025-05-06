import copy
from contextlib import contextmanager
from typing import Any

from fastmcp.settings import settings


@contextmanager
def temporary_settings(**kwargs: Any):
    """
    Temporarily override ControlFlow setting values.

    Args:
        **kwargs: The settings to override, including nested settings.

    Example:
        Temporarily override a setting:
        ```python
        import fastmcp
        from fastmcp.utilities.tests import temporary_settings

        with temporary_settings(log_level='DEBUG'):
            assert fastmcp.settings.settings.log_level == 'DEBUG'
        assert fastmcp.settings.settings.log_level == 'INFO'
        ```
    """
    old_settings = copy.deepcopy(settings.model_dump())

    try:
        # apply the new settings
        for attr, value in kwargs.items():
            if not hasattr(settings, attr):
                raise AttributeError(f"Setting {attr} does not exist.")
            setattr(settings, attr, value)
        yield

    finally:
        # restore the old settings
        for attr in kwargs:
            if hasattr(settings, attr):
                setattr(settings, attr, old_settings[attr])
