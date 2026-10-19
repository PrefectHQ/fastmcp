"""Decorator mixins for LocalProvider.

This module provides mixin classes that add decorator functionality
to LocalProvider for tools, resources, templates, and prompts.
"""

from .prompts import PromptDecoratorMixin
from .resources import ResourceDecoratorMixin
from .tools import ToolDecoratorMixin

__all__ = [
    "PromptDecoratorMixin",
    "ResourceDecoratorMixin",
    "ToolDecoratorMixin",
]
