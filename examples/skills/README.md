# Skills Provider Example

This example demonstrates how to expose agent skills (like Claude Code skills) as MCP resources.

## Structure

```
skills/
├── README.md              # This file
├── server.py              # MCP server that exposes skills
├── client.py              # Example client that discovers and reads skills
└── sample_skills/         # Example skills directory
    ├── pdf-processing/
    │   ├── SKILL.md       # Main skill file
    │   └── reference.md   # Supporting documentation
    └── code-review/
        └── SKILL.md       # Main skill file
```

## Running the Example

1. Start the server:
   ```bash
   uv run python examples/skills/server.py
   ```

2. In another terminal, run the client:
   ```bash
   uv run python examples/skills/client.py
   ```

## How It Works

The skills provider system has a two-layer architecture:

- **`SkillProvider`** - Handles a single skill folder, exposing its files as resources
- **`SkillsDirectoryProvider`** - Scans a directory, creates a `SkillProvider` per folder
- **`ClaudeSkillsProvider`** - Convenience subclass for Claude Code skills (~/.claude/skills/)

For each skill, the provider exposes:
- A **Resource** for the main file (`skill://{name}/SKILL.md`)
- A **Resource** for a synthetic manifest (`skill://{name}/_manifest`)
- Supporting files via **ResourceTemplate** or **Resources** (configurable)

### Progressive Disclosure

When a client lists resources, they see skill names and descriptions (from frontmatter) without fetching the full content. This keeps the discovery cost low.

By default, supporting files are exposed via ResourceTemplate (hidden from `list_resources()`). Set `supporting_files="resources"` to make them visible:

```python
SkillsDirectoryProvider(roots=skills_dir, supporting_files="resources")
```

### The Manifest

The `_manifest` resource provides a JSON listing of all files in a skill:

```json
{
  "skill": "pdf-processing",
  "files": [
    {"path": "SKILL.md", "size": 1234, "hash": "sha256:abc..."},
    {"path": "reference.md", "size": 5678, "hash": "sha256:def..."}
  ]
}
```

This enables clients to download entire skills for local use.

## Usage Examples

### Single Skill

```python
from pathlib import Path
from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillProvider

mcp = FastMCP("My Skill")
mcp.add_provider(SkillProvider(Path.home() / ".claude/skills/pdf-processing"))
mcp.run()
```

### All Skills in a Directory

```python
from fastmcp.server.providers.skills import SkillsDirectoryProvider

mcp = FastMCP("Skills")
mcp.add_provider(SkillsDirectoryProvider(roots=Path.home() / ".claude" / "skills"))
mcp.run()
```

### Claude Code Skills (default location)

```python
from fastmcp import FastMCP
from fastmcp.server.providers.skills import ClaudeSkillsProvider

mcp = FastMCP("My Skills")
mcp.add_provider(ClaudeSkillsProvider())  # Uses ~/.claude/skills/
mcp.run()
```
