"""Assistant prompts."""

from fastmcp.prompts import prompt


@prompt
def code_review(code: str, language: str = "python") -> str:
    """Generate a code review prompt.

    Args:
        code: The code to review.
        language: Programming language (default: python).
    """
    return f"""Please review this {language} code:

```{language}
{code}
```

Focus on:
- Code quality and readability
- Potential bugs or issues
- Performance considerations
- Best practices"""


@prompt(
    name="explain-concept",
    description="Generate a prompt to explain a technical concept.",
    tags={"education", "explanation"},
)
def explain(topic: str, audience: str = "developer") -> str:
    """Generate an explanation prompt.

    Args:
        topic: The concept to explain.
        audience: Target audience level.
    """
    return f"Explain {topic} to a {audience}. Use clear examples and analogies."
