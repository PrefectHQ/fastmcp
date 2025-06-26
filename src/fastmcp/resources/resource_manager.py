"""Resource manager functionality."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from fastmcp.server.server import MountedServer

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
        self._mounted_servers: list[MountedServer] = []
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

    def mount(self, server: MountedServer) -> None:
        """Adds a mounted server as a source for resources and templates."""
        self._mounted_servers.append(server)

    async def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, keyed by URI."""
        return await self._load_resources(via_server=False)

    async def get_resource_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered templates, keyed by URI template."""
        return await self._load_resource_templates(via_server=False)

    async def _load_resources(self, *, via_server: bool = False) -> dict[str, Resource]:
        """
        The single, consolidated recursive method for fetching resources. The 'via_server'
        parameter determines the communication path.

        - via_server=False: Manager-to-manager path for complete, unfiltered inventory
        - via_server=True: Server-to-server path for filtered MCP requests
        """
        all_resources: dict[str, Resource] = {}

        for mounted in self._mounted_servers:
            try:
                if via_server:
                    # Use the server-to-server filtered path
                    child_resources_list = await mounted.server._list_resources()
                    child_resources = {
                        resource.key: resource for resource in child_resources_list
                    }
                else:
                    # Use the manager-to-manager unfiltered path
                    child_resources = (
                        await mounted.server._resource_manager.get_resources()
                    )

                # Apply prefix if needed
                if mounted.prefix:
                    from fastmcp.server.server import add_resource_prefix

                    for uri, resource in child_resources.items():
                        prefixed_uri = add_resource_prefix(
                            uri, mounted.prefix, mounted.resource_prefix_format
                        )
                        # Create a copy of the resource with the prefixed key
                        prefixed_resource = resource.with_key(prefixed_uri)
                        all_resources[prefixed_uri] = prefixed_resource
                else:
                    all_resources.update(child_resources)
            except Exception as e:
                # Skip failed mounts silently, matches existing behavior
                logger.warning(
                    f"Failed to get resources from mounted server '{mounted.prefix}': {e}"
                )
                continue

        # Finally, add local resources, which always take precedence
        all_resources.update(self._resources)
        return all_resources

    async def _load_resource_templates(
        self, *, via_server: bool = False
    ) -> dict[str, ResourceTemplate]:
        """
        The single, consolidated recursive method for fetching templates. The 'via_server'
        parameter determines the communication path.

        - via_server=False: Manager-to-manager path for complete, unfiltered inventory
        - via_server=True: Server-to-server path for filtered MCP requests
        """
        all_templates: dict[str, ResourceTemplate] = {}

        for mounted in self._mounted_servers:
            try:
                if via_server:
                    # Use the server-to-server filtered path
                    child_templates = await mounted.server._list_resource_templates()
                else:
                    # Use the manager-to-manager unfiltered path
                    child_templates = (
                        await mounted.server._resource_manager.list_resource_templates()
                    )
                child_dict = {template.key: template for template in child_templates}

                # Apply prefix if needed
                if mounted.prefix:
                    from fastmcp.server.server import add_resource_prefix

                    for uri_template, template in child_dict.items():
                        prefixed_uri_template = add_resource_prefix(
                            uri_template, mounted.prefix, mounted.resource_prefix_format
                        )
                        # Create a copy of the template with the prefixed key
                        prefixed_template = template.with_key(prefixed_uri_template)
                        all_templates[prefixed_uri_template] = prefixed_template
                else:
                    all_templates.update(child_dict)
            except Exception as e:
                # Skip failed mounts silently, matches existing behavior
                logger.warning(
                    f"Failed to get templates from mounted server '{mounted.prefix}': {e}"
                )
                continue

        # Finally, add local templates, which always take precedence
        all_templates.update(self._templates)
        return all_templates

    async def list_resources(self) -> list[Resource]:
        """
        Lists all resources, applying protocol filtering.
        """
        resources_dict = await self._load_resources(via_server=True)
        return list(resources_dict.values())

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """
        Lists all templates, applying protocol filtering.
        """
        templates_dict = await self._load_resource_templates(via_server=True)
        return list(templates_dict.values())

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
        if settings.deprecation_warnings:
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

    def add_resource(self, resource: Resource) -> Resource:
        """Add a resource to the manager.

        Args:
            resource: A Resource instance to add. The resource's .key attribute
                will be used as the storage key. To overwrite it, call
                Resource.with_key() before calling this method.
        """
        existing = self._resources.get(resource.key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Resource already exists: {resource.key}")
                self._resources[resource.key] = resource
            elif self.duplicate_behavior == "replace":
                self._resources[resource.key] = resource
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Resource already exists: {resource.key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        self._resources[resource.key] = resource
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
        if settings.deprecation_warnings:
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

    def add_template(self, template: ResourceTemplate) -> ResourceTemplate:
        """Add a template to the manager.

        Args:
            template: A ResourceTemplate instance to add. The template's .key attribute
                will be used as the storage key. To overwrite it, call
                ResourceTemplate.with_key() before calling this method.

        Returns:
            The added template. If a template with the same URI already exists,
            returns the existing template.
        """
        existing = self._templates.get(template.key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Template already exists: {template.key}")
                self._templates[template.key] = template
            elif self.duplicate_behavior == "replace":
                self._templates[template.key] = template
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Template already exists: {template.key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        self._templates[template.key] = template
        return template

    async def has_resource(self, uri: AnyUrl | str) -> bool:
        """Check if a resource exists."""
        uri_str = str(uri)

        # First check concrete resources (local and mounted)
        resources = await self.get_resources()
        if uri_str in resources:
            return True

        # Then check templates (local and mounted) only if not found in concrete resources
        templates = await self.get_resource_templates()
        for template_key in templates.keys():
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

        # First check concrete resources (local and mounted)
        resources = await self.get_resources()
        if resource := resources.get(uri_str):
            return resource

        # Then check templates (local and mounted) - use the utility function to match against storage keys
        templates = await self.get_resource_templates()
        for storage_key, template in templates.items():
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
        """
        Internal API for servers: Finds and reads a resource, respecting the
        filtered protocol path.
        """
        uri_str = str(uri)

        # 1. Check local resources first. The server will have already applied its filter.
        if uri_str in self._resources:
            resource = await self.get_resource(uri_str)
            if not resource:
                raise NotFoundError(f"Resource {uri_str!r} not found")

            try:
                return await resource.read()

            # raise ResourceErrors as-is
            except ResourceError as e:
                logger.exception(f"Error reading resource {uri_str!r}")
                raise e

            # Handle other exceptions
            except Exception as e:
                logger.exception(f"Error reading resource {uri_str!r}")
                if self.mask_error_details:
                    # Mask internal details
                    raise ResourceError(f"Error reading resource {uri_str!r}") from e
                else:
                    # Include original error details
                    raise ResourceError(
                        f"Error reading resource {uri_str!r}: {e}"
                    ) from e

        # 1b. Check local templates if not found in concrete resources
        for key, template in self._templates.items():
            if params := match_uri_template(uri_str, key):
                try:
                    resource = await template.create_resource(uri_str, params=params)
                    return await resource.read()
                except ResourceError as e:
                    logger.exception(
                        f"Error reading resource from template {uri_str!r}"
                    )
                    raise e
                except Exception as e:
                    logger.exception(
                        f"Error reading resource from template {uri_str!r}"
                    )
                    if self.mask_error_details:
                        raise ResourceError(
                            f"Error reading resource from template {uri_str!r}"
                        ) from e
                    else:
                        raise ResourceError(
                            f"Error reading resource from template {uri_str!r}: {e}"
                        ) from e

        # 2. Check mounted servers using the filtered protocol path.
        from fastmcp.server.server import has_resource_prefix, remove_resource_prefix

        for mounted in reversed(self._mounted_servers):
            key = uri_str
            try:
                if mounted.prefix:
                    if has_resource_prefix(
                        key,
                        mounted.prefix,
                        mounted.resource_prefix_format,
                    ):
                        key = remove_resource_prefix(
                            key,
                            mounted.prefix,
                            mounted.resource_prefix_format,
                        )
                    else:
                        continue

                try:
                    result = await mounted.server._read_resource(key)
                    return result[0].content
                except NotFoundError:
                    continue
            except NotFoundError:
                continue

        raise NotFoundError(f"Resource {uri_str!r} not found.")
