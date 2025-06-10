"""Resource manager functionality."""

import inspect
import warnings
from collections.abc import Callable
from typing import Any

from pydantic import AnyUrl

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, ResourceError
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import (
    ResourceTemplate,
    match_uri_template,
)
from fastmcp.settings import DuplicateBehavior
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class ResourceManager:
    """Manages FastMCP resources."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
    ):
        """Initialize the ResourceManager.

        Args:
            duplicate_behavior: How to handle duplicate resources
                (warn, error, replace, ignore)
            mask_error_details: Whether to mask error details from exceptions
                other than ResourceError
        """
        self._resources: dict[str, Resource] = {}
        self._templates: dict[str, ResourceTemplate] = {}
        self.mask_error_details = mask_error_details or settings.mask_error_details

        # Default to "warn" if None is provided
        if duplicate_behavior is None:
            duplicate_behavior = "warn"

        if duplicate_behavior not in DuplicateBehavior.__args__:
            raise ValueError(
                f"Invalid duplicate_behavior: {duplicate_behavior}. "
                f"Must be one of: {', '.join(DuplicateBehavior.__args__)}"
            )
        self.duplicate_behavior = duplicate_behavior

    def add_resource_or_template_from_fn(
        self,
        fn: Callable[..., Any],
        uri: str,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
    ) -> Resource | ResourceTemplate:
        """Add a resource or template to the manager from a function.

        Args:
            fn: The function to register as a resource or template
            uri: The URI for the resource or template
            name: Optional name for the resource or template
            description: Optional description of the resource or template
            mime_type: Optional MIME type for the resource or template
            tags: Optional set of tags for categorizing the resource or template

        Returns:
            The added resource or template. If a resource or template with the same URI already exists,
            returns the existing resource or template.
        """
        from fastmcp.server.context import Context

        # Check if this should be a template
        has_uri_params = "{" in uri and "}" in uri
        # check if the function has any parameters (other than injected context)
        has_func_params = any(
            p
            for p in inspect.signature(fn).parameters.values()
            if p.annotation is not Context
        )

        if has_uri_params or has_func_params:
            return self.add_template_from_fn(
                fn, uri, name, description, mime_type, tags
            )
        elif not has_uri_params and not has_func_params:
            return self.add_resource_from_fn(
                fn, uri, name, description, mime_type, tags
            )
        else:
            raise ValueError(
                "Invalid resource or template definition due to a "
                "mismatch between URI parameters and function parameters."
            )

    def add_resource_from_fn(
        self,
        fn: Callable[..., Any],
        uri: str,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
    ) -> Resource:
        """Add a resource to the manager from a function.

        Args:
            fn: The function to register as a resource
            uri: The URI for the resource
            name: Optional name for the resource
            description: Optional description of the resource
            mime_type: Optional MIME type for the resource
            tags: Optional set of tags for categorizing the resource

        Returns:
            The added resource. If a resource with the same URI already exists,
            returns the existing resource.
        """
        # deprecated in 2.7.0
        warnings.warn(
            "add_resource_from_fn is deprecated. Use Resource.from_function() and call add_resource() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        resource = Resource.from_function(
            fn=fn,
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
        )
        return self.add_resource(resource)

    def add_resource(self, resource: Resource, key: str | None = None) -> Resource:
        """Add a resource to the manager.

        Args:
            resource: A Resource instance to add
            key: Optional URI to use as the storage key (if different from resource.uri)
        """
        storage_key = key or str(resource.uri)
        logger.debug(
            "Adding resource",
            extra={
                "uri": resource.uri,
                "storage_key": storage_key,
                "type": type(resource).__name__,
                "resource_name": resource.name,
            },
        )
        existing = self._resources.get(storage_key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Resource already exists: {storage_key}")
                self._resources[storage_key] = resource
            elif self.duplicate_behavior == "replace":
                self._resources[storage_key] = resource
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Resource already exists: {storage_key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        self._resources[storage_key] = resource
        return resource

    def add_template_from_fn(
        self,
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
    ) -> ResourceTemplate:
        """Create a template from a function."""
        # deprecated in 2.7.0
        warnings.warn(
            "add_template_from_fn is deprecated. Use ResourceTemplate.from_function() and call add_template() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        template = ResourceTemplate.from_function(
            fn,
            uri_template=uri_template,
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
        )
        return self.add_template(template)

    def add_template(
        self, template: ResourceTemplate, key: str | None = None
    ) -> ResourceTemplate:
        """Add a template to the manager.

        Args:
            template: A ResourceTemplate instance to add
            key: Optional URI template to use as the storage key (if different from template.uri_template)

        Returns:
            The added template. If a template with the same URI already exists,
            returns the existing template.
        """
        uri_template_str = str(template.uri_template)
        storage_key = key or uri_template_str
        logger.debug(
            "Adding template",
            extra={
                "uri_template": uri_template_str,
                "storage_key": storage_key,
                "type": type(template).__name__,
                "template_name": template.name,
            },
        )
        existing = self._templates.get(storage_key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Template already exists: {storage_key}")
                self._templates[storage_key] = template
            elif self.duplicate_behavior == "replace":
                self._templates[storage_key] = template
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Template already exists: {storage_key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        self._templates[storage_key] = template
        return template

    def has_resource(self, uri: AnyUrl | str) -> bool:
        """Check if a resource exists."""
        uri_str = str(uri)
        if uri_str in self._resources:
            return True
        for template_key in self._templates.keys():
            if match_uri_template(uri_str, template_key):
                return True
        return False

    async def get_resource(self, uri: AnyUrl | str) -> Resource:
        """Get resource by URI, checking concrete resources first, then templates.

        Args:
            uri: The URI of the resource to get

        Raises:
            NotFoundError: If no resource or template matching the URI is found.
        """
        uri_str = str(uri)
        logger.debug("Getting resource", extra={"uri": uri_str})

        # First check concrete resources
        if resource := self._resources.get(uri_str):
            return resource

        # Then check templates - use the utility function to match against storage keys
        for storage_key, template in self._templates.items():
            # Try to match against the storage key (which might be a custom key)
            if params := match_uri_template(uri_str, storage_key):
                try:
                    return await template.create_resource(
                        uri_str,
                        params=params,
                    )
                # Pass through ResourceErrors as-is
                except ResourceError as e:
                    logger.error(f"Error creating resource from template: {e}")
                    raise e
                # Handle other exceptions
                except Exception as e:
                    logger.error(f"Error creating resource from template: {e}")
                    if self.mask_error_details:
                        # Mask internal details
                        raise ValueError("Error creating resource from template") from e
                    else:
                        # Include original error details
                        raise ValueError(
                            f"Error creating resource from template: {e}"
                        ) from e

        raise NotFoundError(f"Unknown resource: {uri_str}")

    async def read_resource(self, uri: AnyUrl | str) -> str | bytes:
        """Read a resource contents."""
        resource = await self.get_resource(uri)

        try:
            return await resource.read()

        # raise ResourceErrors as-is
        except ResourceError as e:
            logger.error(f"Error reading resource {uri!r}: {e}")
            raise e

        # Handle other exceptions
        except Exception as e:
            logger.error(f"Error reading resource {uri!r}: {e}")
            if self.mask_error_details:
                # Mask internal details
                raise ResourceError(f"Error reading resource {uri!r}") from e
            else:
                # Include original error details
                raise ResourceError(f"Error reading resource {uri!r}: {e}") from e

    def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, keyed by URI."""
        return self._resources

    def get_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered templates, keyed by URI template."""
        return self._templates
