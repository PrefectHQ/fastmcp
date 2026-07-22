"""camelCase compatibility bridge for MCP SDK v2.

MCP Python SDK v2 renamed protocol fields from camelCase (`inputSchema`) to
snake_case (`input_schema`). FastMCP returns these SDK models directly from
client calls, middleware hooks, and handler callbacks, so legacy user code that
reads the old camelCase spellings would break.

This module installs warn-once `@property` shims that route a small set of
documented camelCase reads to their snake_case attributes. Only fields users
actually read (per the docs boundary inventory) are bridged; each read emits a
single `FastMCPDeprecationWarning` per (class, name) and returns the correct
value. Installation is idempotent.

The properties are installed unconditionally, but each getter checks the live
`mcp_camelcase_compat` setting at read time: when the setting is enabled it
warns and returns the snake_case value; when disabled it raises `AttributeError`
exactly as if the property were never installed. This makes the setting a
genuine runtime toggle (`fastmcp.settings.mcp_camelcase_compat = False` after
import turns the bridge off) at negligible overhead.

Guards ensure we never shadow a real upstream attribute: if a class already
defines the camelCase name in its own `__dict__` or in its pydantic
`model_fields`, we skip it. The property is a plain descriptor read, so values
survive `model_copy`/`model_validate` (the underlying snake field is what gets
copied/validated; the property reads through it every time).

# TODO(sdk-v2-migration): remove once user code has migrated off camelCase reads.
"""

from __future__ import annotations

import warnings

import mcp_types

from fastmcp.exceptions import FastMCPDeprecationWarning

# Map each SDK model class to the camelCase -> snake_case field reads we bridge.
# Limited to fields FastMCP users actually read (docs boundary inventory).
_ALIASES: dict[type, dict[str, str]] = {
    mcp_types.Tool: {
        "inputSchema": "input_schema",
        "outputSchema": "output_schema",
    },
    mcp_types.ToolAnnotations: {
        "readOnlyHint": "read_only_hint",
        "destructiveHint": "destructive_hint",
        "idempotentHint": "idempotent_hint",
        "openWorldHint": "open_world_hint",
    },
    mcp_types.Resource: {
        "mimeType": "mime_type",
    },
    mcp_types.ResourceTemplate: {
        "mimeType": "mime_type",
        "uriTemplate": "uri_template",
    },
    mcp_types.TextResourceContents: {
        "mimeType": "mime_type",
    },
    mcp_types.BlobResourceContents: {
        "mimeType": "mime_type",
    },
    mcp_types.ImageContent: {
        "mimeType": "mime_type",
    },
    mcp_types.AudioContent: {
        "mimeType": "mime_type",
    },
    mcp_types.CallToolResult: {
        "isError": "is_error",
        "structuredContent": "structured_content",
    },
    mcp_types.Completion: {
        "hasMore": "has_more",
    },
    mcp_types.InitializeResult: {
        "serverInfo": "server_info",
        "protocolVersion": "protocol_version",
    },
    mcp_types.ListToolsResult: {
        "nextCursor": "next_cursor",
    },
    mcp_types.ListResourcesResult: {
        "nextCursor": "next_cursor",
    },
    mcp_types.ListResourceTemplatesResult: {
        "nextCursor": "next_cursor",
        "resourceTemplates": "resource_templates",
    },
    mcp_types.ListPromptsResult: {
        "nextCursor": "next_cursor",
    },
    mcp_types.CreateMessageRequestParams: {
        "systemPrompt": "system_prompt",
        "maxTokens": "max_tokens",
        "stopSequences": "stop_sequences",
        "modelPreferences": "model_preferences",
        "toolChoice": "tool_choice",
    },
    mcp_types.ElicitRequestFormParams: {
        "requestedSchema": "requested_schema",
    },
}

_installed = False


def _make_property(cls_name: str, camel: str, snake: str) -> property:
    """Build a warn-once property routing a camelCase read to a snake attr.

    The getter reads the live `mcp_camelcase_compat` setting on every access: if
    the bridge is disabled it raises `AttributeError` (matching the message
    Python raises for a genuinely missing attribute) so the shim is transparent;
    if enabled it warns once and returns the snake_case value.
    """
    warned = False

    def getter(self: object) -> object:
        nonlocal warned
        import fastmcp

        if not fastmcp.settings.mcp_camelcase_compat:
            raise AttributeError(f"{cls_name!r} object has no attribute {camel!r}")
        if not warned:
            warned = True
            warnings.warn(
                f"Accessing `{cls_name}.{camel}` is deprecated; MCP SDK v2 "
                f"renamed this field to `{snake}`. Update your code to read "
                f"`.{snake}` instead.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        return getattr(self, snake)

    return property(getter)


def install() -> None:
    """Install camelCase compatibility properties on SDK v2 model classes.

    Idempotent. Each bridged read warns once per (class, name) and returns the
    snake_case value. Skips any camelCase name a class already defines to avoid
    shadowing real upstream attributes.
    """
    global _installed
    if _installed:
        return

    for cls, mapping in _ALIASES.items():
        model_fields = getattr(cls, "model_fields", {})
        for camel, snake in mapping.items():
            # Never shadow a real upstream attribute or field.
            if camel in cls.__dict__ or camel in model_fields:
                continue
            setattr(cls, camel, _make_property(cls.__name__, camel, snake))

    _installed = True
