import sys

from .function_prompt import FunctionPrompt, prompt
from .base import Message, Prompt, PromptArgument, PromptMessage, PromptResult

# Backward compat: prompt.py was renamed to base.py to stop Pyright from resolving
# `from fastmcp.prompts import prompt` as the submodule instead of the decorator function.
# This shim keeps `from fastmcp.prompts.prompt import Prompt` working at runtime.
# Safe to remove once we're confident no external code imports from the old path.
sys.modules[f"{__name__}.prompt"] = sys.modules[f"{__name__}.base"]

__all__ = [
    "FunctionPrompt",
    "Message",
    "Prompt",
    "PromptArgument",
    "PromptMessage",
    "PromptResult",
    "prompt",
]
