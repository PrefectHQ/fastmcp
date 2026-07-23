# Vendored test fixtures

## ext-tasks-schema-draft.json

The draft JSON Schema for the `io.modelcontextprotocol/tasks` extension (SEP-2663),
vendored so `fastmcp-tasks` wire models are validated against the real upstream schema.

- Source: https://github.com/modelcontextprotocol/ext-tasks — `schema/draft/schema.json`
- Vendored from commit `2c1425d9a288b9b1f489430fe1e00bb392b47e48` on 2026-07-21
- Re-vendor with:
  `curl -sfL https://raw.githubusercontent.com/modelcontextprotocol/ext-tasks/main/schema/draft/schema.json -o tests/fixtures/ext-tasks-schema-draft.json`

The upstream schema is a draft and may change; when re-vendoring, update the commit
hash above and re-run the schema-validation tests to surface any drift.
