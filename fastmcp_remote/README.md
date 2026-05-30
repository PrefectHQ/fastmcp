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

Use the full MCP endpoint URL for the remote server. Many FastMCP HTTP servers expose MCP at `/mcp`, so a local development server may need `http://localhost:8000/mcp` rather than `http://localhost:8000`.

`fastmcp-remote` starts a local stdio bridge, then connects to the upstream server when the MCP host initializes that bridge. If the upstream server is unavailable, the URL does not point to an MCP endpoint, or authentication cannot complete, initialization fails and the host should report the remote server as failed.

For authenticated MCP servers, OAuth is enabled automatically. To pass a bearer token or other custom header instead, provide a header. The header name ends at the first colon, so values can contain additional colons. Quote the header when the value contains spaces, just like any other shell argument:

```bash
uvx fastmcp-remote https://example.com/mcp \
  --header "Authorization: Bearer <token>"
```

Repeat `--header` to send multiple headers. Header values use `Name: Value` format:

```bash
uvx fastmcp-remote https://example.com/mcp \
  --header "Authorization: Bearer <token>" \
  --header "X-Workspace: production" \
  --header "X-Client-Name: My MCP Host" \
  --header "X-Callback-Url: https://example.com/oauth/callback"
```

Some MCP hosts on Windows have trouble preserving spaces inside command arguments. Put the spaced value in an environment variable and reference it from the header value:

```json
{
  "mcpServers": {
    "remote-api": {
      "command": "uvx",
      "args": [
        "fastmcp-remote",
        "https://example.com/mcp",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <token>"
      }
    }
  }
}
```

Use `--auth none` for unauthenticated development servers:

```bash
uvx fastmcp-remote http://localhost:8000/mcp --auth none
```

## Options

- `--transport`: Choose `http` or `sse`. Defaults to `http`.
- `--header`: Add a header to upstream requests, for example `--header "Authorization: Bearer <token>"`. Values may contain colons. Quote headers whose values contain spaces. Use `${VAR}` to expand environment variables inside values. Repeat for multiple headers.
- `--resource`: Isolate OAuth token storage for a named remote resource.
- `--host`: Set the OAuth callback hostname. Defaults to `localhost`.
- `--auth-timeout`: Set how long to wait for the OAuth callback. Defaults to 300 seconds.
- `--ignore-tool`: Hide tools whose names match a glob pattern.
- `--auth`: Choose `oauth` or `none`. The default uses OAuth unless an `Authorization` header is provided.

OAuth tokens are stored under `~/.fastmcp/remote` by default. Set `FASTMCP_REMOTE_CONFIG_DIR` to use another directory.
