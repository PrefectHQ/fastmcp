# Experimental Skills validation

This experiment targets the stable Skills extension at
`modelcontextprotocol/ext-skills@e083f6d` and builds on FastMCP PR #5057.
It opts the existing `SkillsDirectoryProvider` into filesystem publication and
standard discovery through `experimental_skills=True` and the single
`SkillsExtension`. It does not implement
host activation or the optional directory-read method.

The October 16 review includes the upstream schema correction adding optional
`_meta` to skill entries. The additional directory-read cache requirements do not
apply because this implementation does not advertise directory reads.

The upstream conformance runner at
`modelcontextprotocol/conformance@c37eec888e1c6ff140af79987a40008548b7cc5f`
was run against an HTTP server with a real CRLF `SKILL.md` and supporting file:

```sh
node dist/index.js server --url http://127.0.0.1:18764/mcp \
  --scenario sep-2640-skills-enumeration --spec-version 2026-07-28 --force
node dist/index.js server --url http://127.0.0.1:18764/mcp \
  --scenario sep-2640-skills-manifest --spec-version 2026-07-28 --force
node dist/index.js server --url http://127.0.0.1:18764/mcp \
  --scenario sep-2640-skills-directory --spec-version 2026-07-28 --force
```

Enumeration passed 32 checks. Manifest validation passed four checks with two
SHOULD-level warnings: resources retain their existing names (such as
`review/SKILL.md`) and FastMCP's standard `_meta.fastmcp` metadata instead of
switching to frontmatter names and the Skills-specific metadata prefix. The directory
scenario passed wire validation and skipped optional directory checks because
`directoryRead` is not advertised.

Local protocol tests cover both supporting-file publication modes, nested
skills, binary data, component and global middleware authorization, shared access
policies, additive legacy resources, URI aliases, resource shadowing, duplicate
identities, malformed frontmatter, cache refresh, and nonregular files. Snapshot
tests also verify duplicate YAML keys, recursive aliases, JSON-incompatible
values, raw byte digests, symlinks, and unreadable path rejection.

Lookup scaling tests instrument real filesystem operations in both publication
modes. Listing visits the collection twice and scans each skill twice, independent
of its number of supporting files. A single lookup scans and hashes only the
requested skill, and a resource read performs no recursive scans. The tests also
verify that a newly added skill is immediately addressable.
