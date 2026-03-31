"""Generative UI — let the LLM build custom Prefab UIs on the fly.

The GenerativeUI provider registers two tools:
- generate_prefab_ui: the LLM writes Prefab Python code, it runs in a sandbox, the result renders
- search_prefab_components: the LLM searches the Prefab component library

The generative renderer supports streaming: as the LLM writes code into
the `code` argument, the host forwards partial arguments to the app via
ontoolinputpartial, and the user watches the UI build up in real time.

Usage:
    uv run python generative_ui.py
"""

from fastmcp import FastMCP
from fastmcp.apps.generative import GenerativeUI

mcp = FastMCP("Prefab Studio")
mcp.add_provider(GenerativeUI())

if __name__ == "__main__":
    mcp.run()
