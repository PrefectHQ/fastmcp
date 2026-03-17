import sys

from .function_prompt import FunctionPrompt, prompt
from .base import Message, Prompt, PromptArgument, PromptMessage, PromptResult

# Preserve the old import path (fastmcp.prompts.prompt) for backward compatibility.
# The module was renamed to base.py to avoid shadowing the `prompt` decorator function,
# which caused Pyright to report "Module is not callable" errors.
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
