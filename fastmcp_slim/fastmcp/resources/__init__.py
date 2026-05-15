import sys

from .function_resource import FunctionResource, resource
from .base import Resource, ResourceContent, ResourceResult
from .template import ResourceTemplate
from .types import (
    BinaryResource,
    DirectoryResource,
    FileResource,
    HttpResource,
    TextResource,
)

__all__ = [
    "BinaryResource",
    "DirectoryResource",
    "FileResource",
    "FunctionResource",
    "HttpResource",
    "Resource",
    "ResourceContent",
    "ResourceResult",
    "ResourceTemplate",
    "TextResource",
    "resource",
]

# Backward compat: resource.py was renamed to base.py to stop Pyright from resolving
# `from fastmcp.resources import resource` as the submodule instead of the decorator function.
# This shim keeps `from fastmcp.resources.resource import Resource` working at runtime.
# Safe to remove once we're confident no external code imports from the old path.
sys.modules[f"{__name__}.resource"] = sys.modules[f"{__name__}.base"]
