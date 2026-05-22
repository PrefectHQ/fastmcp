# fastmcp-remote

`fastmcp-remote` is FastMCP's standalone Python stdio bridge for remote MCP servers. It lets MCP clients that launch local stdio processes connect to MCP servers hosted over Streamable HTTP or SSE.

```json
{
  "mcpServers": {
    "linear": {
      "command": "uvx",
      "args": ["fastmcp-remote", "https://mcp.linear.app/mcp"]
    }
  }
}
```

The CLI is powered by [FastMCP](https://gofastmcp.com). Its command shape is inspired by the original [`mcp-remote`](https://github.com/geelen/mcp-remote) npm project, which established the stdio-to-remote bridge pattern used across the MCP ecosystem.

`fastmcp-remote` is intentionally smaller than the general FastMCP CLI. It does not load Python files, discover local MCP configs, prepare project environments, or run development reload loops. It builds one FastMCP client for the URL you provide, exposes that client as a local stdio proxy, and leaves the rest alone.

## Usage

Run a remote MCP server through a local stdio bridge:

```bash
uvx fastmcp-remote https://example.com/mcp
```

For authenticated MCP servers, OAuth is enabled automatically. To pass a bearer token or other custom header instead, provide a header:

```bash
uvx fastmcp-remote https://example.com/mcp \
  --header "Authorization: Bearer <token>"
```

Use `--auth none` for unauthenticated development servers:

```bash
uvx fastmcp-remote http://localhost:8000/mcp --allow-http --auth none
```

## Options

- `--transport`: Choose `http-first`, `http-only`, `sse-first`, or `sse-only`. The `*-first` values select the corresponding transport without probing a second transport.
- `--header`: Add a header to upstream requests. Repeat for multiple headers.
- `--allow-http`: Permit plain HTTP URLs for trusted local or private networks.
- `--resource`: Isolate OAuth token storage for a named remote resource.
- `--host`: Set the OAuth callback hostname. Defaults to `localhost`.
- `--auth-timeout`: Set how long to wait for the OAuth callback. Defaults to 300 seconds.
- `--enable-proxy`: Accepted for npm compatibility. HTTP proxy environment variables are honored by default.
- `--ignore-tool`: Hide tools whose names match a glob pattern.
- `--static-oauth-client-metadata`: Provide OAuth client metadata as JSON or `@/path/to/file.json`.
- `--static-oauth-client-info`: Provide OAuth client information as JSON or `@/path/to/file.json`.
- `--auth`: Choose `oauth` or `none`. The default uses OAuth unless an `Authorization` header is provided.

OAuth tokens are stored under `~/.fastmcp/remote` by default. Set `FASTMCP_REMOTE_CONFIG_DIR` to use another directory.
