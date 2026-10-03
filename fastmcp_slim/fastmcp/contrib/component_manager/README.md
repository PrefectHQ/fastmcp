# Component Manager – Contrib Module for FastMCP

The **Component Manager** provides a unified API for enabling and disabling tools, resources, and prompts at runtime in a FastMCP server. This module is useful for dynamic control over which components are active, enabling advanced features like feature toggling, admin interfaces, or automation workflows.

---

## 🔧 Features

- Enable/disable **tools**, **resources**, and **prompts** via HTTP endpoints.
- Supports **local** and **mounted (server)** components.
- Customizable **API root path**.
- Uses the server's **authentication**, with optional extra **auth scopes**.
- Fully integrates with FastMCP with minimal configuration.

---

## 📦 Installation

This module is part of the `fastmcp.contrib` package. No separate installation is required if you're already using **FastMCP**.

---

## 🚀 Usage

### Basic Setup

```python
from fastmcp import FastMCP
from fastmcp.contrib.component_manager import set_up_component_manager

mcp = FastMCP(name="Component Manager", instructions="This is a test server with component manager.")
set_up_component_manager(server=mcp)
```

The routes use the same authentication as the server's MCP endpoint. This server has no `auth`, so its MCP endpoint and the management routes are both open to anyone who can reach the server. Configure `auth` on any server that is reachable by untrusted clients.

---

## 🔗 API Endpoints

All endpoints are registered at `/` by default, or under the custom path if one is provided.

### Tools

```http
POST /tools/{tool_name}/enable
POST /tools/{tool_name}/disable
```

### Resources

```http
POST /resources/{uri:path}/enable
POST /resources/{uri:path}/disable
```

 * Supports template URIs as well
```http
POST /resources/example://test/{id}/enable
POST /resources/example://test/{id}/disable
```

### Prompts

```http
POST /prompts/{prompt_name}/enable
POST /prompts/{prompt_name}/disable
```
---

#### 🧪 Example Response

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "message": "Disabled tool: example_tool"
}

```

---

## ⚙️ Configuration Options

### Custom Root Path

To mount the API under a different path:

```python
set_up_component_manager(server=mcp, path="/admin")
```

### Authentication and Scopes

When the server has `auth` configured, every management route requires a valid token, with the same scopes as the MCP endpoint. You do not need extra configuration:

```python
mcp = FastMCP(name="Component Manager", instructions="This is a test server with component manager.", auth=auth)
set_up_component_manager(server=mcp)
```

To require more scopes for the management routes than for the MCP endpoint, pass `required_scopes`. A token must have these scopes in addition to the scopes the server's auth provider requires. An empty list requires authentication without extra scopes:

```python
set_up_component_manager(server=mcp, required_scopes=["write", "read"])
```

The auth provider is resolved for each request from the HTTP app that serves the routes. A mounted server's routes therefore use the parent server's auth provider, and a provider assigned after `set_up_component_manager()` still applies. If the serving app has no auth provider, routes with `required_scopes` reject every request with `401 Unauthorized`.

---

## 🧪 Example: Enabling a Tool with Curl

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  http://localhost:8001/tools/example_tool/enable
```

---

## 🧱 Working with Mounted Servers

A mounted server's routes are served by the parent's HTTP app and use the parent's auth provider. You can give each server a different set of extra scopes:

```python
mcp = FastMCP(name="Component Manager", instructions="This is a test server with component manager.", auth=auth)
set_up_component_manager(server=mcp, required_scopes=["mcp:write"])

mounted = FastMCP(name="Component Manager", instructions="This is a test server with component manager.", auth=auth)
set_up_component_manager(server=mounted, required_scopes=["mounted:write"])

mcp.mount(server=mounted, namespace="mo")
```

This allows you to grant different levels of access:

```bash
# Accessing the main server gives you control over both local and mounted components
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  http://localhost:8001/tools/mo_example_tool/enable

# Accessing the mounted server gives you control only over its own components
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  http://localhost:8002/tools/example_tool/enable
```

---

## ⚙️ How It Works

- `set_up_component_manager()` registers HTTP routes for tools, resources, and prompts.
- Each request is checked against the auth provider of the server that serves it, plus any `required_scopes`.
- Each endpoint calls `server.enable()` or `server.disable()` with the component name.
- Returns a success message in JSON.

---

## Maintenance Notice

This module is not officially maintained by the core FastMCP team. It is an independent extension developed by [gorocode](https://github.com/gorocode).

If you encounter any issues or wish to contribute, please feel free to open an issue or submit a pull request, and kindly notify me. I'd love to stay up to date.


## 📄 License

This module follows the license of the main [FastMCP](https://github.com/PrefectHQ/fastmcp) project.