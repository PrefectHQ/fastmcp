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

# Preserve the old import path (fastmcp.resources.resource) for backward compatibility.
# The module was renamed to base.py to avoid shadowing the `resource` decorator function,
# which caused Pyright to report "Module is not callable" errors.
sys.modules[f"{__name__}.resource"] = sys.modules[f"{__name__}.base"]
