"""FileUpload — a Provider that adds drag-and-drop file upload to any server.

Lets users upload files directly to the server through an interactive UI,
bypassing the LLM context window entirely. The LLM can then read and work
with uploaded files through model-visible tools.

Requires ``fastmcp[apps]`` (prefab-ui).

Usage::

    from fastmcp import FastMCP
    from fastmcp.apps import FileUpload

    mcp = FastMCP("My Server")
    mcp.add_provider(FileUpload())

For custom persistence, override the storage methods::

    class S3Upload(FileUpload):
        def on_store(self, files: list[dict]) -> list[dict]:
            # write to S3, return summaries
            ...

        def on_list(self) -> list[dict]:
            # list from S3
            ...

        def on_read(self, name: str) -> dict:
            # read from S3
            ...
"""

from __future__ import annotations

try:
    from prefab_ui.actions import SetState, ShowToast
    from prefab_ui.actions.mcp import CallTool
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import (
        H3,
        Badge,
        Button,
        Card,
        CardContent,
        CardFooter,
        CardHeader,
        Column,
        DropZone,
        Muted,
        Row,
        Separator,
        Small,
        Text,
    )
    from prefab_ui.components.control_flow import Else, ForEach, If
    from prefab_ui.rx import ERROR, RESULT, STATE, Rx
except ImportError as _exc:
    raise ImportError(
        "FileUpload requires prefab-ui. Install with: pip install 'fastmcp[apps]'"
    ) from _exc

import base64
from datetime import datetime
from typing import Any

from fastmcp.apps.app import FastMCPApp

_TEXT_EXTENSIONS = frozenset(
    (".csv", ".json", ".txt", ".md", ".py", ".yaml", ".yml", ".toml")
)


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def _make_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": entry["name"],
        "type": entry["type"],
        "size": entry["size"],
        "size_display": _format_size(entry["size"]),
        "uploaded_at": entry["uploaded_at"],
    }


class FileUpload(FastMCPApp):
    """A Provider that adds file upload capabilities to a server.

    Registers a drag-and-drop UI tool, a backend storage tool, and
    model-visible tools for listing and reading uploaded files.

    Files are scoped by MCP session and stored in memory by default.
    Override ``on_store``, ``on_list``, and ``on_read`` for custom
    persistence (filesystem, S3, database, etc.).

    Example::

        from fastmcp import FastMCP
        from fastmcp.apps import FileUpload

        mcp = FastMCP("My Server")
        mcp.add_provider(FileUpload())
    """

    def __init__(
        self,
        name: str = "Files",
        *,
        max_file_size: int = 10 * 1024 * 1024,
    ) -> None:
        super().__init__(name)
        self._max_file_size = max_file_size

        # Default in-memory store, keyed by session_id
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

        self._register_tools()

    def __repr__(self) -> str:
        return f"FileUpload({self.name!r})"

    # ------------------------------------------------------------------
    # Storage interface — override these for custom persistence
    # ------------------------------------------------------------------

    def _get_session_id(self) -> str:
        try:
            from fastmcp.server.dependencies import get_context

            return get_context().session_id
        except RuntimeError:
            # No active session (e.g. direct call_tool in tests).
            # Fall back to a shared namespace.
            return "__default__"

    def on_store(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Store uploaded files and return summaries.

        Each file dict contains ``name``, ``size``, ``type``, and
        ``data`` (base64-encoded content).

        Override this method for custom persistence. The default
        implementation stores files in memory, scoped by MCP session.

        Returns:
            List of file summary dicts (``name``, ``type``, ``size``,
            ``size_display``, ``uploaded_at``).
        """
        session_id = self._get_session_id()
        session_files = self._store.setdefault(session_id, {})
        for f in files:
            session_files[f["name"]] = {
                "name": f["name"],
                "size": f["size"],
                "type": f["type"],
                "data": f["data"],
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            }
        return [_make_summary(e) for e in session_files.values()]

    def on_list(self) -> list[dict[str, Any]]:
        """List all stored files.

        Override this method for custom persistence. The default
        implementation returns files from the current session.

        Returns:
            List of file summary dicts.
        """
        session_id = self._get_session_id()
        session_files = self._store.get(session_id, {})
        return [_make_summary(e) for e in session_files.values()]

    def on_read(self, name: str) -> dict[str, Any]:
        """Read a file's contents by name.

        Override this method for custom persistence. The default
        implementation reads from the current session's in-memory store.
        Text files are decoded from base64; binary files return a
        truncated base64 preview.

        Returns:
            Dict with file metadata and ``content`` (text) or
            ``content_base64`` (binary preview).

        Raises:
            ValueError: If the file is not found.
        """
        session_id = self._get_session_id()
        session_files = self._store.get(session_id, {})
        if name not in session_files:
            available = list(session_files.keys())
            raise ValueError(f"File {name!r} not found. Available: {available}")
        entry = session_files[name]
        result: dict[str, Any] = {
            "name": entry["name"],
            "size": entry["size"],
            "type": entry["type"],
            "uploaded_at": entry["uploaded_at"],
        }
        is_text = entry["type"].startswith("text/") or any(
            entry["name"].endswith(ext) for ext in _TEXT_EXTENSIONS
        )
        if is_text:
            try:
                result["content"] = base64.b64decode(entry["data"]).decode("utf-8")
            except UnicodeDecodeError:
                result["content_base64"] = entry["data"][:200] + "..."
        else:
            result["content_base64"] = entry["data"][:200] + "..."
        return result

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        provider = self

        @self.tool()
        def store_files(files: list[dict]) -> list[dict]:
            """Store uploaded files. Receives file objects with name, size, type, data (base64)."""
            return provider.on_store(files)

        @self.tool(model=True)
        def list_files() -> list[dict]:
            """List all uploaded files with metadata."""
            return provider.on_list()

        @self.tool(model=True)
        def read_file(name: str) -> dict:
            """Read an uploaded file's contents by name."""
            return provider.on_read(name)

        @self.ui()
        def file_manager() -> PrefabApp:
            """Upload and manage files. Drop files here to send them to the server."""
            with Card(css_class="max-w-2xl mx-auto") as view:
                with CardHeader(), Row(gap=2, align="center"):
                    H3("File Upload")
                    with If(STATE.stored.length()):
                        Badge(
                            STATE.stored.length(),  # ty:ignore[invalid-argument-type]
                            variant="secondary",
                        )

                with CardContent(), Column(gap=4):
                    Muted(
                        "Drop files to upload them to the server. "
                        "The model can then read and analyze them "
                        "without using the context window."
                    )

                    DropZone(
                        name="pending",
                        icon="inbox",
                        label="Drop files here",
                        description=(
                            "Any file type, up to "
                            f"{_format_size(provider._max_file_size)}"
                        ),
                        multiple=True,
                        max_size=provider._max_file_size,
                    )

                    with If(STATE.pending.length()), Column(gap=2):
                        with (
                            ForEach("pending"),
                            Row(gap=2, align="center"),
                            Column(gap=0),
                        ):
                            Small(Rx("$item.name"))  # ty:ignore[invalid-argument-type]
                            Muted(Rx("$item.type"))  # ty:ignore[invalid-argument-type]

                        Button(
                            "Upload to Server",
                            on_click=CallTool(
                                "store_files",
                                arguments={
                                    "files": Rx("pending"),
                                },
                                on_success=[
                                    SetState("stored", RESULT),
                                    SetState("pending", []),
                                    ShowToast(
                                        "Files uploaded!",
                                        variant="success",
                                    ),
                                ],
                                on_error=ShowToast(
                                    ERROR,  # ty:ignore[invalid-argument-type]
                                    variant="error",
                                ),
                            ),
                        )

                    with If(STATE.stored.length()):
                        Separator()
                        Text(
                            "Uploaded",
                            css_class="font-medium text-sm",
                        )
                        with (
                            ForEach("stored") as f,
                            Row(
                                gap=2,
                                align="center",
                                css_class="justify-between",
                            ),
                        ):
                            with Column(gap=0):
                                Small(f.name)  # ty:ignore[invalid-argument-type]
                                Muted(f.uploaded_at)  # ty:ignore[invalid-argument-type]
                            with Row(gap=2):
                                Badge(f.type, variant="secondary")  # ty:ignore[invalid-argument-type]
                                Badge(
                                    f.size_display,  # ty:ignore[invalid-argument-type]
                                    variant="outline",
                                )

                with CardFooter(), Row(align="center", css_class="w-full"):
                    with If(STATE.stored.length()):
                        Muted(
                            f"{STATE.stored.length()}"
                            f" {STATE.stored.length().pluralize('file')}"
                            " on server"
                        )
                    with Else():
                        Muted("No files uploaded yet")

            return PrefabApp(
                view=view,
                state={
                    "pending": [],
                    "stored": provider.on_list(),
                },
            )
