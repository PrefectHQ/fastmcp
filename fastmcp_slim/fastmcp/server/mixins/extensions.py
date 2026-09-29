"""Server-owned extension registration and provider composition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from fastmcp.server.extensions import (
    ServerExtension,
    _extension_dispatch_scope,
    build_method_handler,
    validate_extension_identifier,
    wrap_tool_call_interceptor,
)
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext
    from fastmcp.server.providers import Provider
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


class ExtensionsMixin:
    """Manage explicit and provider-bundled server extensions."""

    def _required_extensions(self: FastMCP) -> Sequence[ServerExtension]:
        """Extensions this server contributes when used as a provider.

        Auto-registerable registrations take precedence over provider defaults,
        preserving this server's configuration through any number of mounts.
        Other extensions remain local to the server they were registered on.
        """
        registered = [e for e in self._extensions.values() if e.auto_register]
        identifiers = {e.identifier for e in registered}
        return [
            *registered,
            *(
                e
                for provider in self.providers
                for e in provider.required_extensions()
                if e.identifier not in identifiers
            ),
        ]

    def add_extension(self: FastMCP, extension: ServerExtension) -> None:
        """Register a server extension (SEP-2133).

        Extensions contribute capabilities, additive request methods, tool-call
        interception, and optional lifespans. The instance is bound to this
        server. Explicit registration replaces a provider-bundled extension
        with the same identifier, even when providers were added first.
        Two explicit registrations with the same identifier are an error.

        Register before serving. Mounted servers contribute separate instances
        of extensions that opt in with `auto_register = True`; other extensions
        must be registered on the server you run.
        """
        identifier = extension.identifier
        validate_extension_identifier(identifier, owner=type(extension).__name__)
        if identifier in self._extensions and identifier not in self._auto_extensions:
            raise ValueError(
                f"An extension with identifier {identifier!r} is already registered."
            )
        self._install_extension(extension)
        self._auto_extensions.discard(identifier)
        self._extension_conflicts.discard(identifier)
        logger.debug(
            "Registered extension %r explicitly on server %r.", identifier, self.name
        )

    def _install_extension(self: FastMCP, extension: ServerExtension) -> None:
        """Wire a registration, removing methods belonging to its predecessor."""
        from fastmcp.server.dependencies import get_server
        from fastmcp.server.mixins.lifespan import _lifespan_root_active

        identifier = extension.identifier
        validate_extension_identifier(identifier, owner=type(extension).__name__)
        if self._extensions_started or self._lifespan_result_set:
            raise RuntimeError(
                f"Cannot register extension {identifier!r}: the server's lifespan "
                "has already started. Register extensions before serving."
            )
        if _lifespan_root_active.get():
            root = get_server()
            if identifier not in root._extensions:
                raise RuntimeError(
                    f"Cannot register extension {identifier!r} on mounted server "
                    f"{self.name!r}: root server {root.name!r} has already started "
                    "its extension lifespans. Register it on the root or finish "
                    "composing providers before serving."
                )
        bound_server = extension._server_ref() if extension._server_ref else None
        if bound_server is not None and bound_server is not self:
            raise ValueError(
                f"Extension {identifier!r} is already bound to another server. "
                "Use a separate instance or clone() for each server."
            )

        extension._bind(self)
        bindings = tuple(extension.methods())
        old_methods = self._extension_methods.get(identifier, ())
        # Automatic registration must not silently shadow an unrelated extension
        # or an existing custom handler. Validate before changing any handlers.
        for binding in bindings:
            if (
                binding.method not in old_methods
                and self._mcp_server.get_request_handler(binding.method) is not None
            ):
                raise ValueError(
                    f"Cannot register extension {identifier!r}: method "
                    f"{binding.method!r} is already registered."
                )
        for method in old_methods:
            # The SDK exposes lookup/registration but no removal API. Removing
            # old bindings is necessary when explicit configuration disables
            # an optional method from the bundled extension.
            self._mcp_server._request_handlers.pop(method, None)
        for binding in bindings:
            self._mcp_server.add_request_handler(
                binding.method, binding.params_type, build_method_handler(binding)
            )
        self._extensions[identifier] = extension
        self._extension_methods[identifier] = tuple(b.method for b in bindings)

    def _register_provider_extensions(self: FastMCP, provider: Provider) -> None:
        """Register all bundled extensions, rolling back if any registration fails."""
        extensions = self._extensions.copy()
        methods = self._extension_methods.copy()
        automatic = self._auto_extensions.copy()
        conflicts = self._extension_conflicts.copy()
        handlers = self._mcp_server._request_handlers.copy()
        registered = False
        try:
            self._register_bundled_extensions(provider)
            registered = True
        finally:
            if not registered:
                self._extensions = extensions
                self._extension_methods = methods
                self._auto_extensions = automatic
                self._extension_conflicts = conflicts
                self._mcp_server._request_handlers = handlers

    def _register_bundled_extensions(self: FastMCP, provider: Provider) -> None:
        for extension in provider.required_extensions():
            identifier = extension.identifier
            validate_extension_identifier(identifier, owner=type(extension).__name__)
            existing = self._extensions.get(identifier)
            if existing is not None and identifier not in self._auto_extensions:
                logger.debug(
                    "Using explicitly registered extension %r on server %r "
                    "instead of the extension bundled with %r.",
                    identifier,
                    self.name,
                    provider,
                )
                continue
            if not extension.auto_register:
                raise ValueError(
                    f"Extension {identifier!r} bundled with {provider!r} does not "
                    "allow automatic registration. Register it explicitly with "
                    "add_extension() or set auto_register = True on the extension."
                )
            clone = extension.clone()
            if clone is extension or clone.identifier != identifier:
                raise ValueError(
                    "clone() must return a separate extension with the same identifier."
                )
            if existing is not None:
                # settings() may derive its answer from self.server. Compare both
                # configurations in the receiving server's context.
                clone._bind(self)
                if (
                    type(existing) is not type(clone)
                    or existing.settings() != clone.settings()
                ) and identifier not in self._extension_conflicts:
                    logger.warning(
                        "Conflicting bundled configurations for extension %r on "
                        "server %r; keeping the first registration. Configure it "
                        "explicitly with add_extension() to choose the settings.",
                        identifier,
                        self.name,
                    )
                    self._extension_conflicts.add(identifier)
                continue
            self._install_extension(clone)
            self._auto_extensions.add(identifier)
            logger.debug(
                "Registered extension %r from %r on server %r automatically.",
                identifier,
                provider,
                self.name,
            )

    def _compose_tool_call_interceptors(
        self: FastMCP, call_next: CallNext[Any, Any]
    ) -> CallNext[Any, Any]:
        """Nest interceptors after middleware, with the first registration outermost."""
        chain = call_next
        delegated = _extension_dispatch_scope.get()
        delegated_identifiers: frozenset[str] = frozenset()
        if delegated is not None and delegated[0] is self:
            delegated_identifiers = delegated[1]
        for extension in reversed(list(self._extensions.values())):
            if (
                extension.auto_register
                and extension.identifier in delegated_identifiers
            ):
                continue
            chain = cast(
                "CallNext[Any, Any]", wrap_tool_call_interceptor(extension, chain)
            )
        return chain
