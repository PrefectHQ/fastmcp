"""Example: Expose prompts as tools using PromptsAsTools transform.

This example shows how to use PromptsAsTools to make prompts accessible
to clients that only support tools (not the prompts protocol).

Run with:
    uv run python examples/prompts_as_tools/server.py
"""

from fastmcp import FastMCP
from fastmcp.server.transforms import PromptsAsTools

mcp = FastMCP("Prompt Tools Demo")


# Simple prompt without arguments
@mcp.prompt
def explain_concept(concept: str) -> str:
    """Explain a programming concept."""
    return f"""Please explain the following programming concept in simple terms:

{concept}

Include:
- A clear definition
- Common use cases
- A simple example
"""


# Prompt with multiple arguments
@mcp.prompt
def analyze_code(code: str, language: str = "python", focus: str = "all") -> str:
    """Analyze code for potential issues."""
    return f"""Analyze this {language} code:

```{language}
{code}
```

Focus on: {focus}

Please identify:
- Potential bugs or errors
- Performance issues
- Code style improvements
- Security concerns
"""


# Prompt with required and optional arguments
@mcp.prompt
def review_pull_request(
    title: str, description: str, diff: str, guidelines: str = ""
) -> str:
    """Review a pull request."""
    guidelines_section = (
        f"\n\nGuidelines to follow:\n{guidelines}" if guidelines else ""
    )

    return f"""Review this pull request:

**Title:** {title}

**Description:**
{description}

**Diff:**
```
{diff}
```{guidelines_section}

Please provide:
- Summary of changes
- Potential issues or concerns
- Suggestions for improvement
- Overall recommendation (approve/request changes)
"""


# Add the transform - this creates list_prompts and get_prompt tools
mcp.add_transform(PromptsAsTools(mcp))


if __name__ == "__main__":
    mcp.run()
