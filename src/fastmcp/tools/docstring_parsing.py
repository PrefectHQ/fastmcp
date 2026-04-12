"""Extract descriptions from function docstrings.

Uses griffelib to parse Google, NumPy, and Sphinx-style docstrings into a
summary description and per-parameter descriptions. The interface is
intentionally narrow — a single function returning two values — so the
implementation can be swapped without touching callers.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from griffe import Docstring, DocstringSectionKind

_PARSERS = ("google", "numpy", "sphinx")

logger = logging.getLogger("griffe")
# Griffe warns about missing type annotations in docstrings, which is noisy
# and irrelevant — we only care about descriptions.
logger.setLevel(logging.ERROR)


def parse_docstring(fn: Callable[..., Any]) -> tuple[str | None, dict[str, str]]:
    """Parse a function's docstring into a summary and parameter descriptions.

    Tries Google, NumPy, and Sphinx parsers in order, using the first one that
    successfully extracts parameter descriptions. If none do, returns the full
    docstring as the description with no parameter descriptions.

    Returns:
        A tuple of (description, {param_name: param_description}).
        Description is None if the function has no docstring.
    """
    doc = inspect.getdoc(fn)
    if not doc:
        return None, {}

    # Try each parser and use the first one that finds parameters.
    for parser in _PARSERS:
        docstring = Docstring(doc, lineno=1, parser=parser)
        sections = docstring.parse()

        description: str | None = None
        params: dict[str, str] = {}

        for section in sections:
            if section.kind == DocstringSectionKind.text and description is None:
                description = section.value
            elif section.kind == DocstringSectionKind.parameters:
                for param in section.value:
                    params[param.name] = param.description

        if params:
            return description, params

    # No parser found parameters — return the full docstring unchanged.
    return doc, {}
