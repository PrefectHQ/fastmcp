# FastMCP Configuration Demo

This example demonstrates the recommended way to configure FastMCP servers using `fastmcp.json`.

## Migration from Dependencies Parameter

Previously (deprecated as of FastMCP 2.11.4), you would specify dependencies in the Python code:

```python
mcp = FastMCP("Demo Server", dependencies=["pyautogui", "Pillow"])
```

Now, dependencies are declared in `fastmcp.json`:

```json
{
  "environment": {
    "dependencies": ["pyautogui", "Pillow"]
  }
}
```

## Running the Server

With the configuration file in place, you can run the server in several ways:

```bash
# Auto-detect fastmcp.json in current directory
cd examples/mcp_server_config_demo
fastmcp run

# Or specify the config file explicitly
fastmcp run examples/mcp_server_config_demo/fastmcp.json

# Or use development mode with the Inspector UI
fastmcp dev examples/mcp_server_config_demo/fastmcp.json
```

## Benefits

- **Single source of truth**: All configuration in one place
- **Environment isolation**: Dependencies are installed in an isolated UV environment
- **No import-time issues**: Dependencies are installed before the server is imported
- **IDE support**: JSON schema provides autocomplete and validation
- **Shareable**: Easy to share complete server configuration with others

## Configuration Structure

The `fastmcp.json` file supports three main sections:

1. **entrypoint** (required): The Python file containing your server
2. **environment** (optional): Python version and dependencies
3. **deployment** (optional): Runtime settings like transport and logging

See the [full documentation](https://gofastmcp.com/docs/deployment/server-configuration) for more details.