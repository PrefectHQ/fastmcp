# Component Manager – Contrib Module for FastMCP

The **Component Manager** provides a unified API for enabling and disabling tools, resources, and prompts at runtime in a FastMCP server. This module is useful for dynamic control over which components are active, enabling advanced features like feature toggling, admin interfaces, or automation workflows.

---

## 🔧 Features

- Enable/disable **tools**, **resources**, and **prompts** via HTTP endpoints.
- Supports **local** and **mounted (server)** components.
- Customizable **API root path**.
- Optional **Auth scopes** for secured access.
- Fully integrates with FastMCP with minimal configuration.

---

## 📦 Installation

This module is part of the `fastmcp.contrib` package. No separate installation is required if you're already using **FastMCP**.

---

## 🚀 Usage

### Basic Setup

```python
from fastmcp import FastMCP
from fastmcp.contrib.component_manager.component_manager import set_up_component_manager

mcp = FastMCP("Component Manager", instructions="This is a test server with component manager.")
set_up_component_manager(server=mcp)
```

---

## 🔗 API Endpoints

By default, all endpoints are registered at `/` by default, or under the custom path if one is provided.

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

 * Works with template URIs too
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

## ⚙️ Configuration Options

### Custom Root Path

To mount the API under a different path:

```python
set_up_component_manager(server=mcp, path="/admin")
```

### Securing Endpoints with Auth Scopes

If your server uses authentication:

```python
mcp = FastMCP("Component Manager", instructions="This is a test server with component manager.", auth=auth)
set_up_component_manager(server=mcp, required_scopes=["tools:write", "tools:read"])
```

---

## 🧪 Example: Enabling a Tool with Curl

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  http://localhost:8001/tools/example_tool/enable
```

---

## ⚙️ How It Works

- `set_up_component_manager()` registers API routes for tools, resources, and prompts.
- The `ComponentService` class exposes async methods to enable/disable components.
- Each endpoint returns a success message in JSON or a 404 error if the component isn't found.

---

## 🧩 Extending

You can subclass `ComponentService` for custom behavior or mount its routes elsewhere as needed.

---

## Maintenance Notice

This module is not officially maintained by the core FastMCP team. It is an independent extension developed by [gorocode](https://github.com/gorocode).

If you encounter any issues or wish to contribute, please feel free to open an issue or submit a pull request, and kindly notify me. I'd love to stay up to date.


## 📄 License

This module follows the license of the main [FastMCP](https://github.com/jlowin/fastmcp) project.