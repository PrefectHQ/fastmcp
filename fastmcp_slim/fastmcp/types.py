"""Reusable type annotations for FastMCP tool parameters.

These types can be used in tool function signatures to influence how
parameters are presented in UIs (e.g. `fastmcp dev apps`) and
serialized in JSON Schema.

Example:

```python
from fastmcp import FastMCP
from fastmcp.types import Textarea

mcp = FastMCP("demo")

@mcp.tool()
def run_query(sql: Textarea) -> str:
    ...
```
"""

from __future__ import annotations

from typing import Annotated

from mcp_types import (
    Annotations as Annotations,
)
from mcp_types import (
    AudioContent as AudioContent,
)
from mcp_types import (
    BlobResourceContents as BlobResourceContents,
)
from mcp_types import (
    CallToolResult as CallToolResult,
)
from mcp_types import (
    Completion as Completion,
)
from mcp_types import (
    ContentBlock as ContentBlock,
)
from mcp_types import (
    CreateMessageResult as CreateMessageResult,
)
from mcp_types import (
    ElicitRequest as ElicitRequest,
)
from mcp_types import (
    ElicitRequestFormParams as ElicitRequestFormParams,
)
from mcp_types import (
    EmbeddedResource as EmbeddedResource,
)
from mcp_types import (
    ErrorData as ErrorData,
)
from mcp_types import (
    GetPromptResult as GetPromptResult,
)
from mcp_types import (
    Icon as Icon,
)
from mcp_types import (
    ImageContent as ImageContent,
)
from mcp_types import (
    InputRequiredResult as InputRequiredResult,
)
from mcp_types import (
    Prompt as Prompt,
)
from mcp_types import (
    PromptMessage as PromptMessage,
)
from mcp_types import (
    ReadResourceResult as ReadResourceResult,
)
from mcp_types import (
    Resource as Resource,
)
from mcp_types import (
    ResourceLink as ResourceLink,
)
from mcp_types import (
    ResourceTemplate as ResourceTemplate,
)
from mcp_types import (
    Root as Root,
)
from mcp_types import (
    SamplingCapability as SamplingCapability,
)
from mcp_types import (
    SamplingMessage as SamplingMessage,
)
from mcp_types import (
    TextContent as TextContent,
)
from mcp_types import (
    TextResourceContents as TextResourceContents,
)
from mcp_types import (
    Tool as Tool,
)
from mcp_types import (
    ToolAnnotations as ToolAnnotations,
)
from mcp_types import (
    ToolResultContent as ToolResultContent,
)
from pydantic import Field

Textarea = Annotated[str, Field(json_schema_extra={"format": "textarea"})]
"""A string rendered as a multiline textarea in form-based UIs.

Produces `"format": "textarea"` in the JSON Schema, which
`fastmcp dev apps` picks up automatically.
"""

__all__ = [
    "Annotations",
    "AudioContent",
    "BlobResourceContents",
    "CallToolResult",
    "Completion",
    "ContentBlock",
    "CreateMessageResult",
    "ElicitRequest",
    "ElicitRequestFormParams",
    "EmbeddedResource",
    "ErrorData",
    "GetPromptResult",
    "Icon",
    "ImageContent",
    "InputRequiredResult",
    "Prompt",
    "PromptMessage",
    "ReadResourceResult",
    "Resource",
    "ResourceLink",
    "ResourceTemplate",
    "Root",
    "SamplingCapability",
    "SamplingMessage",
    "TextContent",
    "TextResourceContents",
    "Textarea",
    "Tool",
    "ToolAnnotations",
    "ToolResultContent",
]
