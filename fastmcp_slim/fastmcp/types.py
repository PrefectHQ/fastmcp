"""Reusable type annotations for FastMCP tool parameters.

These types can be used in tool function signatures to influence how
parameters are presented in UIs (e.g. ``fastmcp dev apps``) and
serialized in JSON Schema.

Example::

    from fastmcp import FastMCP
    from fastmcp.types import Textarea

    mcp = FastMCP("demo")

    @mcp.tool()
    def run_query(sql: Textarea) -> str:
        ...
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

Textarea = Annotated[str, Field(json_schema_extra={"format": "textarea"})]
"""A string rendered as a multiline textarea in form-based UIs.

Produces ``"format": "textarea"`` in the JSON Schema, which
``fastmcp dev apps`` picks up automatically.
"""

__all__ = ["Textarea"]
