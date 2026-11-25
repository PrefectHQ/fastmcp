# Skills over MCP

The server publishes the two sample folders with `SkillsDirectoryProvider`. Each
`SKILL.md` declares its name and description; supporting files retain their
exact bytes.

Run the client from the repository root:

```bash
uv run python -m examples.skills.client
```

It connects to the example server in memory, discovers entries through the
Skills extension, verifies each complete skill and exports it to a temporary
directory. The temporary copies are removed when the example exits. No local
agent's skills folder is changed.

The last connection doesn't negotiate Skills. `read_skill()` downloads the same
content through the `_manifest` resource instead.

To launch the server over stdio for another MCP client:

```bash
uv run python -m examples.skills.server
```

The provider reads the folders once at startup. Pass `reload=True` to pick up
edits on every request while you're writing a skill.

See [Skills over MCP](https://gofastmcp.com/servers/skills) for custom loaders,
access control, mounts and installing skills into a local agent.
