"""Extract descriptions from function docstrings.

Uses griffelib to parse Google, NumPy, and Sphinx-style docstrings. The
interface is intentionally narrow — a single function returning a
`ParsedDocstring` — so the implementation can be swapped without touching
callers.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_PARSERS = ("google", "numpy", "sphinx")

logger = logging.getLogger("griffe")
# Griffe warns about missing type annotations in docstrings, which is noisy
# and irrelevant — we only care about descriptions.
logger.setLevel(logging.ERROR)


@dataclass(frozen=True)
class ParsedDocstring:
    """The extracted description and per-parameter descriptions from a docstring."""

    description: str | None = None
    parameters: dict[str, str] = field(default_factory=dict)


def parse_docstring(fn: Callable[..., Any]) -> ParsedDocstring:
    """Parse a function's docstring into a summary and parameter descriptions.

    Tries Google, NumPy, and Sphinx parsers in order, using the first one that
    successfully extracts parameter descriptions. If none do, returns the full
    docstring as the description with no parameter descriptions.
    """
    doc = inspect.getdoc(fn)
    if not doc:
        return ParsedDocstring()

    # Griffe is only needed for functions that actually have docstrings. This
    # keeps its parser and model graph out of ordinary server startup.
    from griffe import Docstring, DocstringSectionKind

    # Try each parser and use the first one that finds parameters.
    fallback_description: str | None = None
    for parser in _PARSERS:
        docstring = Docstring(doc, lineno=1, parser=parser)
        sections = docstring.parse()

        description: str | None = None
        parameters: dict[str, str] = {}

        for section in sections:
            if section.kind == DocstringSectionKind.text and description is None:
                description = section.value
            elif section.kind == DocstringSectionKind.parameters:
                for param in section.value:
                    parameters[param.name] = param.description

        if parameters:
            return ParsedDocstring(description=description, parameters=parameters)

        # Track the best description across parsers for the fallback path.
        # Prefer the shortest text section, as that indicates the parser
        # correctly separated the summary from Returns/Args/Yields sections.
        if description and (fallback_description is None or len(description) < len(fallback_description)):
            fallback_description = description

    # No parser found parameters — return the extracted description (without
    # Returns/Args/Yields sections) rather than the full raw docstring.
    return ParsedDocstring(description=fallback_description or doc)
