"""Resource manager functionality."""

import copy
import inspect
import re
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

    def __init__(self, duplicate_behavior: DuplicateBehavior = DuplicateBehavior.WARN):
        self._resources: dict[str, Resource] = {}
        self._templates: dict[str, ResourceTemplate] = {}
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

        if has_uri_params and has_func_params:
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

    def add_resource(self, resource: Resource) -> Resource:
        """Add a resource to the manager.

        Args:
            resource: A Resource instance to add
        """
        logger.debug(
            "Adding resource",
            extra={
                "uri": resource.uri,
                "type": type(resource).__name__,
                "resource_name": resource.name,
            },
        )
        existing = self._resources.get(str(resource.uri))
        if existing:
            if self.duplicate_behavior == DuplicateBehavior.WARN:
                logger.warning(f"Resource already exists: {resource.uri}")
                self._resources[str(resource.uri)] = resource
            elif self.duplicate_behavior == DuplicateBehavior.REPLACE:
                self._resources[str(resource.uri)] = resource
            elif self.duplicate_behavior == DuplicateBehavior.ERROR:
                raise ValueError(f"Resource already exists: {resource.uri}")
            elif self.duplicate_behavior == DuplicateBehavior.IGNORE:
                pass
        self._resources[str(resource.uri)] = resource
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

        # Validate that URI params match function params
        uri_params = set(re.findall(r"{(\w+)}", uri_template))
        func_params = set(inspect.signature(fn).parameters.keys())

        if uri_params != func_params:
            raise ValueError(
                f"Mismatch between URI parameters {uri_params} "
                f"and function parameters {func_params}"
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
            template: A ResourceTemplate instance to add

        Returns:
            The added template. If a template with the same URI already exists,
            returns the existing template.
        """
        logger.debug(
            "Adding resource",
            extra={
                "uri": template.uri_template,
                "type": type(template).__name__,
                "resource_name": template.name,
            },
        )
        existing = self._templates.get(str(template.uri_template))
        if existing:
            if self.duplicate_behavior == DuplicateBehavior.WARN:
                logger.warning(f"Resource already exists: {template.uri_template}")
                self._templates[str(template.uri_template)] = template
            elif self.duplicate_behavior == DuplicateBehavior.REPLACE:
                self._templates[str(template.uri_template)] = template
            elif self.duplicate_behavior == DuplicateBehavior.ERROR:
                raise ValueError(f"Resource already exists: {template.uri_template}")
            elif self.duplicate_behavior == DuplicateBehavior.IGNORE:
                pass
        self._templates[template.uri_template] = template
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

    def list_resources(self) -> list[Resource]:
        """List all registered resources."""
        logger.debug("Listing resources", extra={"count": len(self._resources)})
        return list(self._resources.values())

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
            # Create prefixed URI and copy the resource with the new URI
            prefixed_uri = f"{prefix}{uri}" if prefix else uri

            new_resource = copy.copy(resource)
            new_resource.uri = AnyUrl(prefixed_uri)

            # Store directly in resources dictionary
            self.add_resource(new_resource)
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
            # Create prefixed URI template and copy the template with the new URI template
            prefixed_uri_template = (
                f"{prefix}{uri_template}" if prefix else uri_template
            )

            new_template = copy.copy(template)
            new_template.uri_template = prefixed_uri_template

            # Store directly in templates dictionary
            self.add_template(new_template)
            logger.debug(
                f'Imported template "{uri_template}" as "{prefixed_uri_template}"'
            )
