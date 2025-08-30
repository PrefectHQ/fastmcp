import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.sources.base import Source

logger = get_logger(__name__)


class FileSystemSource(Source):
    """Source for local Python files."""

    type: Literal["filesystem"] = "filesystem"

    path: str = Field(description="Path to Python file containing the server")
    entrypoint: str | None = Field(
        default=None,
        description="Name of server instance or factory function (a no-arg function that returns a FastMCP server)",
    )

    @field_validator("path", mode="before")
    @classmethod
    def parse_path_with_object(cls, v: str) -> str:
        """Parse path:object syntax and extract the object name.

        This validator runs before the model is created, allowing us to
        handle the "file.py:object" syntax at the model boundary.
        """
        if isinstance(v, str) and ":" in v:
            # Check if it's a Windows path (e.g., C:\...)
            has_windows_drive = len(v) > 1 and v[1] == ":"

            # Only split if colon is not part of Windows drive
            if ":" in (v[2:] if has_windows_drive else v):
                # This path has an object specification
                # We'll handle it in __init__ by setting entrypoint
                return v
        return v

    def __init__(self, **data: Any) -> None:
        """Initialize FileSystemSource, handling path:object syntax."""
        # Check if path contains an object specification
        if "path" in data and isinstance(data["path"], str) and ":" in data["path"]:
            path_str = data["path"]
            # Check if it's a Windows path (e.g., C:\...)
            has_windows_drive = len(path_str) > 1 and path_str[1] == ":"

            # Only split if colon is not part of Windows drive
            if ":" in (path_str[2:] if has_windows_drive else path_str):
                file_str, obj = path_str.rsplit(":", 1)
                data["path"] = file_str
                # Only set entrypoint if not already provided
                if "entrypoint" not in data or data["entrypoint"] is None:
                    data["entrypoint"] = obj

        super().__init__(**data)

    async def load_server(self) -> Any:
        """Load server from filesystem."""
        # Resolve the file path
        file_path = Path(self.path).expanduser().resolve()
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            sys.exit(1)
        if not file_path.is_file():
            logger.error(f"Not a file: {file_path}")
            sys.exit(1)

        # Import the module
        module = self._import_module(file_path)

        # Find the server object
        server = await self._find_server_object(module, file_path)

        return server

    def _import_module(self, file_path: Path) -> Any:
        """Import a Python module from a file path.

        Args:
            file_path: Path to the Python file

        Returns:
            The imported module
        """
        # Add parent directory to Python path so imports can be resolved
        file_dir = str(file_path.parent)
        if file_dir not in sys.path:
            sys.path.insert(0, file_dir)

        # Import the module
        spec = importlib.util.spec_from_file_location("server_module", file_path)
        if not spec or not spec.loader:
            logger.error("Could not load module", extra={"file": str(file_path)})
            sys.exit(1)

        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["server_module"] = module  # Register in sys.modules
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        return module

    async def _find_server_object(self, module: Any, file_path: Path) -> Any:
        """Find the server object in the module.

        Args:
            module: The imported Python module
            file_path: Path to the file (for error messages)

        Returns:
            The server object (or result of calling a factory function)
        """
        # Avoid circular import by importing here
        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.server.server import FastMCP

        # If entrypoint is specified, use it
        if self.entrypoint:
            # Handle module:object syntax (though this is legacy)
            if ":" in self.entrypoint:
                module_name, object_name = self.entrypoint.split(":", 1)
                try:
                    import importlib

                    server_module = importlib.import_module(module_name)
                    obj = getattr(server_module, object_name, None)
                except ImportError:
                    logger.error(
                        f"Could not import module '{module_name}'",
                        extra={"file": str(file_path)},
                    )
                    sys.exit(1)
            else:
                # Just object name
                obj = getattr(module, self.entrypoint, None)

            if obj is None:
                logger.error(
                    f"Server object '{self.entrypoint}' not found",
                    extra={"file": str(file_path)},
                )
                sys.exit(1)

            return await self._resolve_factory(obj, file_path, self.entrypoint)

        # No entrypoint specified, try common server names
        for name in ["mcp", "server", "app"]:
            if hasattr(module, name):
                obj = getattr(module, name)
                if isinstance(obj, FastMCP | FastMCP1x):
                    return await self._resolve_factory(obj, file_path, name)

        # No server found
        logger.error(
            f"No server object found in {file_path}. Please either:\n"
            "1. Use a standard variable name (mcp, server, or app)\n"
            "2. Specify the entrypoint name in fastmcp.json or use `file.py:object` syntax as your path.",
            extra={"file": str(file_path)},
        )
        sys.exit(1)

    async def _resolve_factory(self, obj: Any, file_path: Path, name: str) -> Any:
        """Resolve a server object or factory function to a server instance.

        Args:
            obj: The object that might be a server or factory function
            file_path: Path to the file for error messages
            name: Name of the object for error messages

        Returns:
            A server instance
        """
        # Avoid circular import by importing here
        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.server.server import FastMCP

        # Check if it's a function or coroutine function
        if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
            logger.debug(f"Found factory function '{name}' in {file_path}")

            try:
                if inspect.iscoroutinefunction(obj):
                    # Async factory function
                    server = await obj()
                else:
                    # Sync factory function
                    server = obj()

                # Validate the result is a FastMCP server
                if not isinstance(server, FastMCP | FastMCP1x):
                    logger.error(
                        f"Factory function '{name}' must return a FastMCP server instance, "
                        f"got {type(server).__name__}",
                        extra={"file": str(file_path)},
                    )
                    sys.exit(1)

                logger.debug(f"Factory function '{name}' created server: {server.name}")
                return server

            except Exception as e:
                logger.error(
                    f"Failed to call factory function '{name}': {e}",
                    extra={"file": str(file_path)},
                )
                sys.exit(1)

        # Not a function, return as-is (should be a server instance)
        return obj
