"""Minimal example demonstrating a @app=True tool with arguments.

Usage:
    uv run python greet_server.py
"""

from __future__ import annotations

from typing import Literal

from prefab_ui.components import Badge, Column, Heading, Muted

from fastmcp import FastMCP

mcp = FastMCP("Greeter")

GREETINGS: dict[str, str] = {
    "English": "Hello",
    "Spanish": "¡Hola",
    "French": "Bonjour",
    "Japanese": "こんにちは",
    "Arabic": "مرحبا",
}


@mcp.tool(app=True)
def greet(
    name: str,
    language: Literal["English", "Spanish", "French", "Japanese", "Arabic"] = "English",
) -> Column:
    """Greet someone in their language."""
    word = GREETINGS[language]
    with Column(gap=3, css_class="p-8") as view:
        Heading(f"{word}, {name}!")
        Muted("Greeting rendered by FastMCP")
        Badge(language)
    return view


if __name__ == "__main__":
    mcp.run()
