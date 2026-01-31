# QR Code MCP App

An MCP App server that generates QR codes with an interactive viewer UI. Ported from the [ext-apps QR server example](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/qr-server) to demonstrate FastMCP's MCP Apps support.

## What it demonstrates

- Linking a tool to a `ui://` resource via `ToolUI`
- Serving embedded HTML with the `@modelcontextprotocol/ext-apps` JS SDK from CDN
- Declaring CSP resource domains via `ResourceCSP`
- Returning `ImageContent` (base64 PNG) from a tool

## Setup

```bash
cd examples/apps/qr_server
uv sync
```

## Usage

```bash
uv run python qr_server.py
```

Or install it into an MCP client:

```bash
fastmcp install stdio fastmcp.json
```

## How it works

The server registers one tool (`generate_qr`) and one resource (`ui://qr-server/view.html`). The tool generates a QR code as a base64 PNG image. The resource serves an HTML page that uses the MCP Apps JS SDK to receive the tool result and display the image in a sandboxed iframe.

The HTML loads the ext-apps SDK from unpkg, so the resource declares `csp=ResourceCSP(resource_domains=["https://unpkg.com"])` to allow the host to set the appropriate Content-Security-Policy.
