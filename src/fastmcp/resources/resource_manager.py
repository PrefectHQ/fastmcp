"""Resource manager functionality."""

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import AnyUrl

from fastmcp.exceptions import ResourceError
from fastmcp.resources import FunctionResource, Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.settings import DuplicateBehavior
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class ResourceManager:
    """Manages FastMCP resources."""

    def __init__(self, duplicate_behavior: DuplicateBehavior | None = None):
        self._resources: dict[str, Resource] = {}
        self._templates: dict[str, ResourceTemplate] = {}

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
        # Check if this should be a template
        has_uri_params = "{" in uri and "}" in uri
        has_func_params = bool(inspect.signature(fn).parameters)

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
        resource = FunctionResource(
            uri=AnyUrl(uri),
            name=name,
            description=description,
            mime_type=mime_type or "text/plain",
            fn=fn,
            tags=tags or set(),
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

    async def get_resource(self, uri: AnyUrl | str) -> Resource | None:
        """Get resource by URI, checking concrete resources first, then templates."""
        uri_str = str(uri)
        logger.debug("Getting resource", extra={"uri": uri_str})

        # First check concrete resources
        if resource := self._resources.get(uri_str):
            return resource

        # Then check templates
        for template in self._templates.values():
            if params := template.matches(uri_str):
                try:
                    return await template.create_resource(uri_str, params)
                except Exception as e:
                    raise ValueError(f"Error creating resource from template: {e}")

        raise ResourceError(f"Unknown resource: {uri}")

    def get_resources(self) -> dict[str, Resource]:
        """Get all registered resources, keyed by URI."""
        return self._resources

    def list_resources(self) -> list[Resource]:
        """List all registered resources."""
        logger.debug("Listing resources", extra={"count": len(self._resources)})
        return list(self._resources.values())

    def get_templates(self) -> dict[str, ResourceTemplate]:
        """Get all registered templates, keyed by URI template."""
        return self._templates

    def list_templates(self) -> list[ResourceTemplate]:
        """List all registered templates."""
        logger.debug("Listing templates", extra={"count": len(self._templates)})
        return list(self._templates.values())

    def import_resources(
        self, manager: "ResourceManager", prefix: str | None = None
    ) -> None:
        """Import resources from another resource manager.

        Resources are imported with a prefixed URI if a prefix is provided. For example,
        if a resource has URI "data://users" and you import it with prefix "app+", the
        imported resource will have URI "app+data://users". If no prefix is provided,
        the original URI is used.

        Args:
            manager: The ResourceManager to import from
            prefix: A prefix to apply to the resource URIs, including the delimiter.
                   For example, "app+" would result in URIs like "app+data://users".
                   If None, the original URI is used.
        """
        for uri, resource in manager._resources.items():
            # Create prefixed URI and import the resource with the new URI as the storage key
            prefixed_uri = f"{prefix}{uri}" if prefix else uri
            self.add_resource(resource, key=prefixed_uri)
            logger.debug(f'Imported resource "{uri}" as "{prefixed_uri}"')

    def import_templates(
        self, manager: "ResourceManager", prefix: str | None = None
    ) -> None:
        """Import resource templates from another resource manager.

        Templates are imported with a prefixed URI template if a prefix is provided.
        For example, if a template has URI template "data://users/{id}" and you import
        it with prefix "app+", the imported template will have URI template
        "app+data://users/{id}". If no prefix is provided, the original URI template is used.

        Args:
            manager: The ResourceManager to import templates from
            prefix: A prefix to apply to the template URIs, including the delimiter.
                   For example, "app+" would result in URI templates like "app+data://users/{id}".
                   If None, the original URI template is used.
        """
        for uri_template, template in manager._templates.items():
            # Create prefixed URI template and import the template with the new URI as the storage key
            prefixed_uri_template = (
                f"{prefix}{uri_template}" if prefix else uri_template
            )
            self.add_template(template, key=prefixed_uri_template)
            logger.debug(
                f'Imported template "{uri_template}" as "{prefixed_uri_template}"'
            )
