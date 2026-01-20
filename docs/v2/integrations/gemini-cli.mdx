---
title: Gemini CLI 🤝 FastMCP
sidebarTitle: Gemini CLI
description: Install and use FastMCP servers in Gemini CLI
icon: message-smile
tag: NEW
---

import { VersionBadge } from "/snippets/version-badge.mdx"
import { LocalFocusTip } from "/snippets/local-focus.mdx"

<LocalFocusTip />

Gemini CLI supports MCP servers through multiple transport methods including STDIO, SSE, and HTTP, allowing you to extend Gemini's capabilities with custom tools, resources, and prompts from your FastMCP servers.

## Requirements

This integration uses STDIO transport to run your FastMCP server locally. For remote deployments, you can run your FastMCP server with HTTP or SSE transport and configure it directly using Gemini CLI's built-in MCP management commands.

## Create a Server

The examples in this guide will use the following simple dice-rolling server, saved as `server.py`.

```python server.py
import random
from fastmcp import FastMCP

mcp = FastMCP(name="Dice Roller")

@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(1, 6) for _ in range(n_dice)]

if __name__ == "__main__":
    mcp.run()
```

## Install the Server

### FastMCP CLI
<VersionBadge version="2.13.0" />

The easiest way to install a FastMCP server in Gemini CLI is using the `fastmcp install gemini-cli` command. This automatically handles the configuration, dependency management, and calls Gemini CLI's built-in MCP management system.

```bash
fastmcp install gemini-cli server.py
```

The install command supports the same `file.py:object` notation as the `run` command. If no object is specified, it will automatically look for a FastMCP server object named `mcp`, `server`, or `app` in your file:

```bash
# These are equivalent if your server object is named 'mcp'
fastmcp install gemini-cli server.py
fastmcp install gemini-cli server.py:mcp

# Use explicit object name if your server has a different name
fastmcp install gemini-cli server.py:my_custom_server
```

The command will automatically configure the server with Gemini CLI's `gemini mcp add` command.

#### Dependencies

FastMCP provides flexible dependency management options for your Gemini CLI servers:

**Individual packages**: Use the `--with` flag to specify packages your server needs. You can use this flag multiple times:

```bash
fastmcp install gemini-cli server.py --with pandas --with requests
```

**Requirements file**: If you maintain a `requirements.txt` file with all your dependencies, use `--with-requirements` to install them:

```bash
fastmcp install gemini-cli server.py --with-requirements requirements.txt
```

**Editable packages**: For local packages under development, use `--with-editable` to install them in editable mode:

```bash
fastmcp install gemini-cli server.py --with-editable ./my-local-package
```

Alternatively, you can use a `fastmcp.json` configuration file (recommended):

```json fastmcp.json
{
  "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
  "source": {
    "path": "server.py",
    "entrypoint": "mcp"
  },
  "environment": {
    "dependencies": ["pandas", "requests"]
  }
}
```


#### Python Version and Project Configuration

Control the Python environment for your server with these options:

**Python version**: Use `--python` to specify which Python version your server requires. This ensures compatibility when your server needs specific Python features:

```bash
fastmcp install gemini-cli server.py --python 3.11
```

**Project directory**: Use `--project` to run your server within a specific project context. This tells `uv` to use the project's configuration files and virtual environment:

```bash
fastmcp install gemini-cli server.py --project /path/to/my-project
```

#### Environment Variables

If your server needs environment variables (like API keys), you must include them:

```bash
fastmcp install gemini-cli server.py --server-name "Weather Server" \
  --env API_KEY=your-api-key \
  --env DEBUG=true
```

Or load them from a `.env` file:

```bash
fastmcp install gemini-cli server.py --server-name "Weather Server" --env-file .env
```

<Warning>
**Gemini CLI must be installed**. The integration looks for the Gemini CLI and uses the `gemini mcp add` command to register servers.
</Warning>

### Manual Configuration

For more control over the configuration, you can manually use Gemini CLI's built-in MCP management commands. This gives you direct control over how your server is launched:

```bash
# Add a server with custom configuration
gemini mcp add dice-roller uv -- run --with fastmcp fastmcp run server.py

# Add with environment variables
gemini mcp add weather-server -e API_KEY=secret -e DEBUG=true uv -- run --with fastmcp fastmcp run server.py

# Add with specific scope (user, or project)
gemini mcp add my-server --scope user uv -- run --with fastmcp fastmcp run server.py
```

You can also manually specify Python versions and project directories in your Gemini CLI commands:

```bash
# With specific Python version
gemini mcp add ml-server uv -- run --python 3.11 --with fastmcp fastmcp run server.py

# Within a project directory
gemini mcp add project-server uv -- run --project /path/to/project --with fastmcp fastmcp run server.py
```

## Using the Server

Once your server is installed, you can start using your FastMCP server with Gemini CLI.

Try asking Gemini something like:

> "Roll some dice for me"

Gemini will automatically detect your `roll_dice` tool and use it to fulfill your request.

Gemini CLI can now access all the tools and prompts you've defined in your FastMCP server. 

If your server provides prompts, you can use them as slash commands with `/prompt_name`.
