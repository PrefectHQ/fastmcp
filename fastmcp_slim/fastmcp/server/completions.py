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

from collections.abc import Awaitable, Callable

import mcp_types

CompletionReference = mcp_types.PromptReference | mcp_types.ResourceTemplateReference
"""The reference a completion request targets: a prompt or a resource template."""

CompletionValues = mcp_types.Completion | list[str] | tuple[str, ...] | None
"""What a completion handler may return.

- ``Completion`` — used verbatim (carries the optional ``total`` / ``has_more``
  pagination hints).
- ``list[str]`` / ``tuple[str, ...]`` — wrapped into a ``Completion``. A bare
  ``str`` is deliberately excluded: it satisfies ``Sequence[str]`` but is almost
  always a mistake, and ``normalize_completion`` rejects it at runtime — naming
  concrete collections keeps the annotation and the runtime guard in agreement.
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


# The MCP completion contract caps `values` at 100 candidates per response.
MAX_COMPLETION_VALUES = 100


def normalize_completion(result: CompletionValues) -> mcp_types.Completion:
    """Coerce a handler's return value into a wire ``Completion``.

    A returned ``str`` is rejected: it is almost always a mistake (the value
    would iterate into one-character candidates), so it raises rather than
    silently producing surprising output.

    The MCP contract caps a completion at 100 values, so a longer result is
    truncated to the first 100 with ``has_more`` set — a handler that returns
    thousands of matches emits a conforming response rather than an oversized
    one that strict clients reject.
    """
    if result is None:
        return mcp_types.Completion(values=[])
    if isinstance(result, str):
        raise TypeError(
            "A completion handler returned a str; return a list of strings "
            "(for example, [value]) or a Completion instead."
        )
    if isinstance(result, mcp_types.Completion):
        completion = result
    else:
        completion = mcp_types.Completion(values=list(result))

    if len(completion.values) > MAX_COMPLETION_VALUES:
        total = (
            completion.total if completion.total is not None else len(completion.values)
        )
        return mcp_types.Completion(
            values=completion.values[:MAX_COMPLETION_VALUES],
            total=total,
            has_more=True,
        )
    return completion
