<div align="center">

<!-- omit in toc -->

<picture>
  <source width="550" media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/f-watercolor-waves-dark-2.png">
  <source width="550" media="(prefers-color-scheme: light)" srcset="docs/assets/brand/f-watercolor-waves-2.png">
  <img width="550" alt="FastMCP Logo" src="docs/assets/brand/f-watercolor-waves-2.png">
</picture>

# FastMCP 🚀

<strong>Move fast and make things.</strong>

*Made with 💙 by [Prefect](https://www.prefect.io/)*

[![Docs](https://img.shields.io/badge/docs-gofastmcp.com-blue)](https://gofastmcp.com)
[![Discord](https://img.shields.io/badge/community-discord-5865F2?logo=discord&logoColor=white)](https://discord.gg/uu8dJCgttd)
[![PyPI - Version](https://img.shields.io/pypi/v/fastmcp.svg)](https://pypi.org/project/fastmcp)
[![Tests](https://github.com/jlowin/fastmcp/actions/workflows/run-tests.yml/badge.svg)](https://github.com/jlowin/fastmcp/actions/workflows/run-tests.yml)
[![License](https://img.shields.io/github/license/jlowin/fastmcp.svg)](https://github.com/jlowin/fastmcp/blob/main/LICENSE)

<a href="https://trendshift.io/repositories/13266" target="_blank"><img src="https://trendshift.io/api/badge/repositories/13266" alt="jlowin%2Ffastmcp | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
</div>

---

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) provides a standardized way to connect AI agents to tools and data. FastMCP makes it easy to build MCP applications with clean, Pythonic code:

```python
from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")

@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

if __name__ == "__main__":
    mcp.run()
```

## Why FastMCP

MCP lets you give agents access to your tools and data. But building an effective MCP server is harder than it looks.

Give your agent too much—hundreds of tools, verbose responses—and it gets overwhelmed. Give it too little and it can't do its job. The protocol itself is complex, with layers of serialization, validation, and error handling that have nothing to do with your business logic. And the spec keeps evolving; what worked last month might already need updating.

The real challenge isn't implementing the protocol. It's delivering **the right information at the right time**.

That's the problem FastMCP solves—and why it's become the standard. FastMCP 1.0 was incorporated into the official MCP SDK in 2024. Today, the actively maintained standalone project is downloaded a million times a day, and some version of FastMCP powers 70% of MCP servers across all languages.

The framework is built on three abstractions that map to the decisions you actually need to make:

- **Components** are what you expose: tools, resources, and prompts. Wrap a Python function, and FastMCP handles the schema, validation, and docs.
- **Providers** are where components come from: decorated functions, files on disk, OpenAPI specs, remote servers—your logic can live anywhere.
- **Transforms** shape what clients see: namespacing, filtering, authorization, versioning. The same server can present differently to different users.

These compose cleanly, so complex patterns don't require complex code. And because FastMCP is opinionated about the details, like serialization, error handling, and protocol compliance, **best practices are the path of least resistance**. You focus on your logic; the MCP part just works.

**Move fast and make things.**

## Installation

> [!Note]
> FastMCP 3.0 is currently in beta. Install with: `pip install fastmcp==3.0.0b1`
>
> For production systems requiring stability, pin to v2: `pip install 'fastmcp<3'`

We recommend installing FastMCP with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install fastmcp
```

For full installation instructions, including verification and upgrading, see the [**Installation Guide**](https://gofastmcp.com/getting-started/installation).

## 📚 Documentation

FastMCP's complete documentation is available at **[gofastmcp.com](https://gofastmcp.com)**, including detailed guides, API references, and advanced patterns.

Documentation is also available in [llms.txt format](https://llmstxt.org/), which is a simple markdown standard that LLMs can consume easily:

- [`llms.txt`](https://gofastmcp.com/llms.txt) is essentially a sitemap, listing all the pages in the documentation.
- [`llms-full.txt`](https://gofastmcp.com/llms-full.txt) contains the entire documentation. Note this may exceed the context window of your LLM.

**Community:** Join our [Discord server](https://discord.gg/uu8dJCgttd) to connect with other FastMCP developers and share what you're building.

## Contributing

We welcome contributions! See the [Contributing Guide](https://gofastmcp.com/development/contributing) for setup instructions, testing requirements, and PR guidelines.
