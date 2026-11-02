# Skills over MCP

The server loads the two sample folders into `Skill` values and publishes them
through `SkillCatalogProvider`. Each `SKILL.md` declares its name and description;
supporting files retain their exact bytes.

Run the client from the repository root:

```bash
uv run python -m examples.skills.client
```

It connects to the example server in memory, discovers entries through the
Skills extension, verifies each complete skill and exports it to a temporary
directory. The temporary copies are removed when the example exits. No local
agent's skills folder is changed.

The last connection uses resource-only discovery with `list_skill_summaries()`.
`read_skill()` downloads the same content through the `_manifest` fallback, so
this also works when the connection does not negotiate Skills.

To launch the server over stdio for another MCP client:

```bash
uv run python -m examples.skills.server
```

For live recursive folder discovery, use
`SkillsDirectoryProvider(root, experimental_skills=True)`. The catalog in this
example holds snapshots: changing a source file does not mutate an existing
`Skill`.

See [Skills over MCP](https://gofastmcp.com/servers/skills) for custom loaders,
access control, mounts and installing skills into a local agent.
