"""Server-side argument completion for FastMCP.

A completion request names a reference — a specific prompt or resource
template — and the argument being completed, plus a context of the argument
values already supplied. The server answers with candidate string values.

FastMCP surfaces this as a single server-level handler registered with
``@mcp.completion``, mirroring the MCP SDK's own ``completion/complete`` shape
and FastMCP's client-side ``Client.complete()``. The handler receives the
reference, the argument, and the optional context, and returns candidates for
whichever reference/argument pair it recognizes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import mcp_types

CompletionReference = mcp_types.PromptReference | mcp_types.ResourceTemplateReference
"""The reference a completion request targets: a prompt or a resource template."""

CompletionValues = mcp_types.Completion | Sequence[str] | None
"""What a completion handler may return.

- ``Completion`` — used verbatim (carries the optional ``total`` / ``has_more``
  pagination hints).
- ``Sequence[str]`` — wrapped into a ``Completion``.
- ``None`` — treated as "no candidates" (an empty completion).
"""

CompletionHandler = Callable[
    [
        CompletionReference,
        mcp_types.CompletionArgument,
        mcp_types.CompletionContext | None,
    ],
    Awaitable[CompletionValues] | CompletionValues,
]
"""A server's completion handler.

Called with the reference, the argument being completed, and the optional
context of already-supplied argument values. May be sync or async.
"""


def normalize_completion(result: CompletionValues) -> mcp_types.Completion:
    """Coerce a handler's return value into a wire ``Completion``.

    A returned ``str`` is rejected: it is almost always a mistake (the value
    would iterate into one-character candidates), so it raises rather than
    silently producing surprising output.
    """
    if result is None:
        return mcp_types.Completion(values=[])
    if isinstance(result, mcp_types.Completion):
        return result
    if isinstance(result, str):
        raise TypeError(
            "A completion handler returned a str; return a list of strings "
            "(for example, [value]) or a Completion instead."
        )
    return mcp_types.Completion(values=list(result))
