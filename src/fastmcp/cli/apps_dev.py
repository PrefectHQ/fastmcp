"""Dev server for previewing FastMCPApp UIs locally.

Starts the user's MCP server on a configurable port, then starts a lightweight
Starlette dev server that:

  - Serves a Prefab-based tool picker at GET /
  - Proxies /mcp to the user's server (avoids browser CORS restrictions)
  - Serves the AppBridge host page at GET /launch

The host page uses @modelcontextprotocol/ext-apps to connect to the MCP server
and render the selected UI tool inside an iframe.

Startup sequence
----------------
1. Download ext-apps app-bridge.js from npm and patch its bare
   ``@modelcontextprotocol/sdk/…`` imports to use concrete esm.sh URLs.
2. Detect the exact Zod v4 module URL that esm.sh serves for that SDK version
   and build an import-map entry that redirects the broken ``v4.mjs`` (which
   only re-exports ``{z, default}``) to ``v4/classic/index.mjs`` (which
   correctly exports every named Zod v4 function).  Import maps apply to the
   full module graph in the document, including cross-origin esm.sh modules.
3. Serve both the patched JS and the import-map JSON from the dev server.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import signal
import sys
import tarfile
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpcore
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# MCP message log (captures proxy traffic for the dev UI log panel)
# ---------------------------------------------------------------------------


class _MessageLog:
    """In-memory buffer of MCP JSON-RPC messages flowing through the proxy."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._counter = 0
        self._request_methods: dict[int | str, str] = {}
        self._request_times: dict[int | str, float] = {}

    def log_request(self, body: dict[str, Any]) -> None:
        method = body.get("method", "unknown")
        jsonrpc_id = body.get("id")
        timestamp = time.time()
        if jsonrpc_id is not None:
            self._request_methods[jsonrpc_id] = method
            self._request_times[jsonrpc_id] = timestamp
        self._counter += 1
        self._entries.append(
            {
                "id": self._counter,
                "timestamp": timestamp,
                "direction": "request",
                "method": method,
                "body": body,
            }
        )

    def log_response(self, body: dict[str, Any]) -> None:
        # Server-initiated notifications have "method" but no "id"
        if "method" in body and "id" not in body:
            self._counter += 1
            self._entries.append(
                {
                    "id": self._counter,
                    "timestamp": time.time(),
                    "direction": "notification",
                    "method": body.get("method", "unknown"),
                    "body": body,
                }
            )
            return

        jsonrpc_id = body.get("id")
        method = (
            self._request_methods.pop(jsonrpc_id, None)
            if jsonrpc_id is not None
            else None
        )
        request_time = (
            self._request_times.pop(jsonrpc_id, None)
            if jsonrpc_id is not None
            else None
        )
        timestamp = time.time()
        duration_ms = (
            round((timestamp - request_time) * 1000, 1) if request_time else None
        )
        self._counter += 1
        self._entries.append(
            {
                "id": self._counter,
                "timestamp": timestamp,
                "direction": "response",
                "method": method,
                "body": body,
                "duration_ms": duration_ms,
            }
        )

    def get_since(self, since_id: int = 0) -> list[dict[str, Any]]:
        return [e for e in self._entries if e["id"] > since_id]

    def log_bridge(self, body: dict[str, Any]) -> None:
        method = body.get("method", "unknown")
        self._counter += 1
        self._entries.append(
            {
                "id": self._counter,
                "timestamp": time.time(),
                "direction": "bridge",
                "method": method,
                "body": body,
            }
        )

    def clear(self) -> None:
        self._entries.clear()
        self._request_methods.clear()
        self._request_times.clear()


def _log_response_bytes(log: _MessageLog, raw: bytes, content_type: str) -> None:
    """Parse accumulated proxy response bytes and log as message entries."""
    if not raw:
        return
    try:
        if "text/event-stream" in content_type:
            for line in raw.decode("utf-8", errors="replace").splitlines():
                if line.startswith("data: "):
                    with contextlib.suppress(json.JSONDecodeError):
                        log.log_response(json.loads(line[6:]))
        else:
            body = json.loads(raw)
            if isinstance(body, list):
                for item in body:
                    log.log_response(item)
            else:
                log.log_response(body)
    except (json.JSONDecodeError, TypeError):
        pass


_EXT_APPS_VERSION = "1.0.1"
# Pin to the SDK version ext-apps 1.0.1 was compiled against so the client
# and transport modules are API-compatible with the app-bridge internals.
_MCP_SDK_VERSION = "1.25.2"

# ---------------------------------------------------------------------------
# Shared AppBridge host shell
# ---------------------------------------------------------------------------

# Both the picker and the app launcher use the same host-page structure: an
# iframe that hosts a Prefab renderer, wired to the MCP server via AppBridge.
# The only differences are (a) which URL loads in the iframe and (b) what
# oninitialized does.
#
# app-bridge.js is served locally (see _fetch_app_bridge_bundle).
# Client/Transport are loaded from esm.sh.
# The import map (injected as {import_map_tag}) patches the broken esm.sh
# Zod v4 module so all Zod named exports are visible to the SDK at runtime.

_HOST_SHELL = """\
<!doctype html>
<html>
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
{import_map_tag}
  <style>
    html, body {{ margin: 0; padding: 0; width: 100%; height: 100vh; overflow: hidden; }}
    #app-frame {{ width: 100%; height: 100%; border: none; display: none; }}
    #status {{
      display: flex; align-items: center; justify-content: center; height: 100vh;
      font-family: system-ui, sans-serif; color: #666; font-size: 1rem;
    }}
  </style>
</head>
<body>
  <div id="status" style="display:{status_display}">{status_text}</div>
  <iframe id="app-frame" style="display:{frame_display}"></iframe>
  <script type="module">
    import {{ AppBridge, PostMessageTransport }}
      from "/js/app-bridge.js";
    import {{ Client }}
      from "https://esm.sh/@modelcontextprotocol/sdk@{mcp_sdk_version}/client/index.js";
    import {{ StreamableHTTPClientTransport }}
      from "https://esm.sh/@modelcontextprotocol/sdk@{mcp_sdk_version}/client/streamableHttp.js";

    const status = document.getElementById("status");
    const iframe  = document.getElementById("app-frame");

    async function main() {{
      const client = new Client({{ name: "fastmcp-dev", version: "1.0.0" }});
      await client.connect(
        new StreamableHTTPClientTransport(new URL("/mcp", window.location.origin))
      );
      const serverCaps = client.getServerCapabilities();

      // Set iframe src after adding load listener to avoid race condition
      const loaded = new Promise(r => iframe.addEventListener("load", r, {{ once: true }}));
      iframe.src = {iframe_src_json};
      await loaded;

      const transport = new PostMessageTransport(
        iframe.contentWindow,
        iframe.contentWindow,
      );
      const bridge = new AppBridge(
        client,
        {{ name: "fastmcp-dev", version: "1.0.0" }},
        {{
          openLinks: {{}},
          serverTools: serverCaps?.tools,
          serverResources: serverCaps?.resources,
        }},
        {{
          hostContext: {{
            theme: window.matchMedia("(prefers-color-scheme: dark)").matches
              ? "dark" : "light",
            platform: "web",
            containerDimensions: {{ maxHeight: 8000 }},
            displayMode: "inline",
            availableDisplayModes: ["inline", "fullscreen"],
          }},
        }},
      );

      bridge.onmessage = async () => ({{}});
      {on_open_link}
      {on_initialized}

      await bridge.connect(transport);
    }}

    main().catch(err => {{
      console.error(err);
      if (status) {{
        status.style.display = "flex";
        status.textContent = "Error: " + err.message;
      }}
    }});
  </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Host page HTML
# ---------------------------------------------------------------------------

_HOST_HTML_TEMPLATE = """\
<!doctype html>
<html>
<head>
  <meta charset="UTF-8">
  <title>FastMCP Dev — {tool_name}</title>
{import_map_tag}
  <style>
    html, body {{ margin: 0; padding: 0; width: 100%; height: 100vh; overflow: hidden; }}
    #app-frame {{ width: 100%; height: 100%; border: none; display: none; }}
    #status {{
      display: flex; align-items: center; justify-content: center; height: 100vh;
      font-family: system-ui, sans-serif; color: #666; font-size: 1rem;
    }}
  </style>
</head>
<body>
  <div id="status">Launching {tool_name}…</div>
  <iframe id="app-frame"></iframe>
  <script type="module">
    import {{ AppBridge, PostMessageTransport, getToolUiResourceUri }}
      from "/js/app-bridge.js";
    import {{ Client }}
      from "https://esm.sh/@modelcontextprotocol/sdk@{mcp_sdk_version}/client/index.js";
    import {{ StreamableHTTPClientTransport }}
      from "https://esm.sh/@modelcontextprotocol/sdk@{mcp_sdk_version}/client/streamableHttp.js";

    const toolName = {tool_name_json};
    const toolArgs = {tool_args_json};
    const status = document.getElementById("status");
    const iframe  = document.getElementById("app-frame");

    async function main() {{
      // Connect to the proxied MCP server (same-origin, no CORS needed)
      const client = new Client({{ name: "fastmcp-dev", version: "1.0.0" }});
      await client.connect(
        new StreamableHTTPClientTransport(new URL("/mcp", window.location.origin))
      );

      // Find the tool and its UI resource URI
      const {{ tools }} = await client.listTools();
      const tool = tools.find(t => t.name === toolName);
      if (!tool) throw new Error("Tool not found: " + toolName);

      const uiUri = getToolUiResourceUri(tool);
      if (!uiUri) throw new Error("Tool has no UI resource: " + toolName);

      // The Prefab renderer calls earlyBridge.connect() at module-load time
      // (synchronously, before React mounts) so it sends its ui/initialize
      // request very early — potentially before the iframe's load event fires.
      // Fix: create the AppBridge and call bridge.connect() BEFORE loading the
      // iframe so our window.addEventListener is registered first.  We pass
      // null as the PostMessageTransport source so early messages from the
      // not-yet-known renderer window are not filtered out.  After the iframe
      // loads we update transport.eventTarget / .eventSource to the real
      // renderer window; the load-event microtask always runs before the
      // message macrotask, so the response reaches the correct window.
      const serverCaps = client.getServerCapabilities();
      const transport = new PostMessageTransport(iframe.contentWindow, null);
      const bridge = new AppBridge(
        client,
        {{ name: "fastmcp-dev", version: "1.0.0" }},
        {{
          openLinks: {{}},
          serverTools: serverCaps?.tools,
          serverResources: serverCaps?.resources,
        }},
        {{
          hostContext: {{
            theme: window.matchMedia("(prefers-color-scheme: dark)").matches
              ? "dark" : "light",
            platform: "web",
            containerDimensions: {{ maxHeight: 8000 }},
            displayMode: "inline",
            availableDisplayModes: ["inline", "fullscreen"],
          }},
        }},
      );

      bridge.onopenlink = async ({{ url }}) => {{
        window.open(url, "_blank", "noopener,noreferrer");
        return {{}};
      }};
      bridge.onmessage = async () => ({{}});

      // When the View initializes: send input args, call the tool, send result
      bridge.oninitialized = async () => {{
        await bridge.sendToolInput({{ arguments: toolArgs }});
        const result = await client.callTool({{ name: toolName, arguments: toolArgs }});
        await bridge.sendToolResult(result);
        status.style.display = "none";
        iframe.style.display = "block";
        // Prevent horizontal scrollbar when vertical scrollbar appears
        try {{ iframe.contentDocument.documentElement.style.overflowX = "hidden"; }} catch(e) {{}}
      }};

      // Start listening before the iframe loads
      await bridge.connect(transport);

      // Now load the renderer HTML via the server-side proxy
      const frameUrl = "/ui-resource?uri=" + encodeURIComponent(uiUri);
      const loaded = new Promise(r => {{ iframe.addEventListener("load", r, {{ once: true }}); }});
      iframe.src = frameUrl;
      await loaded;

      // Update transport to the real renderer window.  This microtask runs
      // before the ui/initialize message macrotask, ensuring the response
      // is dispatched to the correct window.
      transport.eventTarget = iframe.contentWindow;
      transport.eventSource = iframe.contentWindow;
    }}

    main().catch(err => {{
      status.textContent = "Error: " + err.message;
      console.error(err);
    }});
  </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Dev log panel (injected into host pages)
# ---------------------------------------------------------------------------

_LOG_PANEL_HTML = """\
<style>
  #mcp-log-panel {
    position: fixed; top: 0; left: 0; bottom: 0; width: 360px;
    z-index: 10000;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    font-size: 12px; background: #1e1e2e; color: #cdd6f4;
    border-right: 1px solid #45475a;
    display: flex; flex-direction: column;
  }
  #mcp-log-panel.hidden { display: none; }
  #app-frame {
    width: 100% !important; height: 100% !important;
    margin-left: 0 !important;
  }
  #mcp-log-resize {
    position: absolute; right: -3px; top: 0; bottom: 0; width: 6px;
    cursor: col-resize; z-index: 1;
  }
  #mcp-log-resize:hover, #mcp-log-resize.active { background: #585b70; }
  #mcp-log-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 12px; background: #181825;
    border-bottom: 1px solid #45475a; flex-shrink: 0;
  }
  #mcp-log-brand {
    display: flex; align-items: center; gap: 8px;
  }
  #mcp-log-brand svg { flex-shrink: 0; }
  #mcp-log-brand-text {
    font-weight: 700; font-size: 13px; color: #cdd6f4;
    letter-spacing: -0.3px;
  }
  #mcp-log-count-badge {
    font-size: 11px; color: #6c7086; font-weight: 400;
  }
  #mcp-log-actions { display: flex; gap: 6px; }
  #mcp-log-actions button {
    background: #313244; color: #cdd6f4; border: 1px solid #45475a;
    padding: 2px 8px; border-radius: 3px; cursor: pointer;
    font-size: 11px; font-family: inherit;
  }
  #mcp-log-actions button:hover { background: #45475a; }
  #mcp-log-entries { flex: 1; overflow-y: auto; }
  .log-entry {
    padding: 6px 12px; border-bottom: 1px solid #232334; cursor: pointer;
  }
  .log-entry:hover { background: #313244; }
  .log-entry.error { background: rgba(243, 139, 168, 0.08); }
  .log-entry.error:hover { background: rgba(243, 139, 168, 0.14); }
  .log-entry.error .log-method { color: #f38ba8; }
  .log-primary {
    display: flex; justify-content: space-between;
    align-items: baseline; gap: 8px;
  }
  .log-left {
    display: flex; gap: 6px; align-items: baseline; min-width: 0;
  }
  .log-dir { flex-shrink: 0; }
  .log-dir.request { color: #89b4fa; }
  .log-dir.response { color: #a6e3a1; }
  .log-dir.error { color: #f38ba8; }
  .log-dir.bridge { color: #cba6f7; }
  .log-dir.notification { color: #fab387; }
  .log-method {
    color: #f9e2af; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .log-meta { color: #6c7086; font-size: 11px; white-space: nowrap; flex-shrink: 0; }
  .log-subtitle {
    color: #a6adc8; font-size: 11px; padding-left: 22px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    margin-top: 1px;
  }
  .log-detail {
    display: none; padding: 8px 12px 4px 22px; background: #11111b;
    white-space: pre-wrap; word-break: break-all;
    color: #bac2de; font-size: 11px; line-height: 1.4;
    margin-top: 4px; border-radius: 4px;
  }
  .log-entry.expanded .log-detail { display: block; }
  @keyframes log-flash {
    from { background: rgba(137, 180, 250, 0.22); }
    to { background: transparent; }
  }
  @keyframes log-flash-error {
    from { background: rgba(243, 139, 168, 0.25); }
    to { background: rgba(243, 139, 168, 0.08); }
  }
  .log-entry.new { animation: log-flash 2s ease-out; }
  .log-entry.error.new { animation: log-flash-error 2s ease-out; }
  .log-copy {
    opacity: 0; transition: opacity 0.15s;
    background: #313244; color: #a6adc8; border: 1px solid #45475a;
    padding: 1px 6px; border-radius: 3px; cursor: pointer;
    font-size: 10px; font-family: inherit; flex-shrink: 0;
  }
  .log-entry:hover .log-copy { opacity: 1; }
  .log-copy:hover { background: #45475a; color: #cdd6f4; }
  #mcp-log-open {
    position: fixed; bottom: 12px; left: 12px; z-index: 10000;
    background: #181825; color: #cdd6f4; border: 1px solid #45475a;
    padding: 6px 12px; border-radius: 6px; cursor: pointer;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    font-size: 11px; display: block;
  }
  #mcp-log-open:hover { background: #313244; }
  #mcp-log-filters {
    display: flex; gap: 8px; align-items: center;
    padding: 6px 12px; border-bottom: 1px solid #45475a; flex-shrink: 0;
  }
  .log-seg {
    display: inline-flex; border: 1px solid #45475a; border-radius: 6px;
    overflow: hidden;
  }
  .log-seg button {
    background: transparent; color: #6c7086; border: none;
    border-right: 1px solid #45475a; padding: 3px 10px; cursor: pointer;
    font-size: 10px; font-family: inherit; transition: all 0.15s;
  }
  .log-seg button:last-child { border-right: none; }
  .log-seg button:hover { background: rgba(205, 214, 244, 0.06); }
  .log-seg button.active[data-filter="tools"] { background: rgba(137, 180, 250, 0.15); color: #89b4fa; }
  .log-seg button.active[data-filter="notifications"] { background: rgba(250, 179, 135, 0.15); color: #fab387; }
  .log-seg button.active[data-filter="bridge"] { background: rgba(203, 166, 247, 0.15); color: #cba6f7; }
  .log-seg button.active[data-filter="errors"] { background: rgba(243, 139, 168, 0.15); color: #f38ba8; }
  #mcp-log-filters-label {
    font-size: 9px; color: #6c7086; text-transform: uppercase;
    letter-spacing: 0.5px; font-weight: 600;
  }
  #mcp-log-level-select {
    background: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 3px 8px; cursor: pointer;
    font-size: 10px; font-family: inherit;
  }
  #mcp-log-level-select option { background: #1e1e2e; }
  .log-level {
    font-size: 9px; padding: 0 5px; border-radius: 3px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;
    flex-shrink: 0; line-height: 16px;
  }
  .log-level-debug { background: #313244; color: #6c7086; }
  .log-level-info { background: rgba(137, 180, 250, 0.15); color: #89b4fa; }
  .log-level-warning { background: rgba(249, 226, 175, 0.15); color: #f9e2af; }
  .log-level-error { background: rgba(243, 139, 168, 0.15); color: #f38ba8; }
  .log-level-notice { background: rgba(148, 226, 213, 0.15); color: #94e2d5; }
  .log-level-critical { background: rgba(243, 139, 168, 0.2); color: #f38ba8; }
  .log-level-alert { background: rgba(243, 139, 168, 0.25); color: #f38ba8; }
  .log-level-emergency { background: rgba(243, 139, 168, 0.3); color: #f38ba8; }
</style>
<div id="mcp-log-panel" class="hidden">
  <div id="mcp-log-resize"></div>
  <div id="mcp-log-header">
    <div id="mcp-log-brand">
      <svg width="20" height="20" viewBox="0 0 196 196" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M145.747 44.611L145.355 44.3877L144.96 44.611L86.0283 78.5276V171.267L86.4014 171.499L99.6674 179.667V86.3859L159 52.2379L145.747 44.611Z" fill="#cdd6f4"/><path d="M121.616 30.2714L121.224 30.0454L120.832 30.2714L61.8975 64.188V156.928L62.2732 157.156L75.5393 165.325V72.0463L134.869 37.8983L121.616 30.2714Z" fill="#cdd6f4"/><path d="M97.4894 16.3818L97.0973 16.1558L96.7025 16.3818L37.7705 50.3038V142.066L51.4096 150.463V58.1567L110.742 24.0086L97.4894 16.3818Z" fill="#cdd6f4"/><path d="M131.23 113.671L124.979 117.266L124.584 117.494V117.5L116.796 121.987L110.547 125.581L110.152 125.807V141.51L144.564 121.709V121.698L158.999 113.394V97.6851L139.277 109.034L131.23 113.671Z" fill="#cdd6f4"/></svg>
      <span id="mcp-log-brand-text">FastMCP Apps</span>
      <span id="mcp-log-count-badge">\u00b7 <span id="mcp-log-count">0</span></span>
    </div>
    <div id="mcp-log-actions">
      <button id="mcp-log-reset" onclick="window.location.href='/'">&#8592; Back</button>
      <script>if (window.location.pathname === "/") document.getElementById("mcp-log-reset").style.display = "none";</script>
      <button id="mcp-log-clear">Clear</button>
      <button id="mcp-log-close">\u00d7</button>
    </div>
  </div>
  <div id="mcp-log-filters">
    <span id="mcp-log-filters-label">Show</span>
    <div class="log-seg">
      <button class="active" data-filter="tools">Tools</button>
      <button class="active" data-filter="notifications">Logs</button>
      <button class="active" data-filter="bridge">Host</button>
      <button class="active" data-filter="errors">Errors</button>
    </div>
    <select id="mcp-log-level-select">
      <option value="debug">Debug+</option>
      <option value="info">Info+</option>
      <option value="warning">Warn+</option>
      <option value="error">Error+</option>
      <option value="critical">Critical+</option>
    </select>
  </div>
  <div id="mcp-log-entries"></div>
</div>
<button id="mcp-log-open">MCP Log</button>
<script>
(function() {
  var lastId = 0, totalCount = 0, panelWidth = 360;
  var panel = document.getElementById("mcp-log-panel");
  var entries = document.getElementById("mcp-log-entries");
  var countEl = document.getElementById("mcp-log-count");
  var openBtn = document.getElementById("mcp-log-open");
  var resizeHandle = document.getElementById("mcp-log-resize");
  var allFilterKeys = ["tools", "notifications", "bridge", "errors"];

  function syncURL() {
    var params = new URLSearchParams(window.location.search);
    if (!panel.classList.contains("hidden")) {
      params.set("log", "open");
    } else {
      params.delete("log");
    }
    var on = [];
    for (var i = 0; i < allFilterKeys.length; i++) {
      if (activeFilters[allFilterKeys[i]]) on.push(allFilterKeys[i]);
    }
    if (on.length === allFilterKeys.length) {
      params.delete("filters");
    } else {
      params.set("filters", on.join(","));
    }
    if (minLevel === 0) {
      params.delete("level");
    } else {
      params.set("level", levelOrder[minLevel]);
    }
    var qs = params.toString();
    var url = window.location.pathname + (qs ? "?" + qs : "");
    history.replaceState(null, "", url);
  }

  function setFrameLayout(w) {
    var frame = document.getElementById("app-frame");
    if (!frame) return;
    frame.style.setProperty("width", w, "important");
    frame.style.setProperty("margin-left", w === "100%" ? "0" : panelWidth + "px", "important");
  }

  document.getElementById("mcp-log-close").addEventListener("click", function() {
    panel.classList.add("hidden");
    openBtn.style.display = "block";
    setFrameLayout("100%");
    syncURL();
  });

  openBtn.addEventListener("click", function() {
    panel.classList.remove("hidden");
    openBtn.style.display = "none";
    setFrameLayout("calc(100% - " + panelWidth + "px)");
    entries.scrollTop = entries.scrollHeight;
    syncURL();
  });

  resizeHandle.addEventListener("mousedown", function(e) {
    e.preventDefault();
    resizeHandle.classList.add("active");
    var frame = document.getElementById("app-frame");
    if (frame) frame.style.pointerEvents = "none";
    document.addEventListener("mousemove", onResize);
    document.addEventListener("mouseup", stopResize);
  });

  function onResize(e) {
    var w = Math.max(200, Math.min(e.clientX, window.innerWidth * 0.8));
    panelWidth = w;
    panel.style.width = w + "px";
    setFrameLayout("calc(100% - " + w + "px)");
  }

  function stopResize() {
    resizeHandle.classList.remove("active");
    var frame = document.getElementById("app-frame");
    if (frame) frame.style.pointerEvents = "";
    document.removeEventListener("mousemove", onResize);
    document.removeEventListener("mouseup", stopResize);
  }

  document.getElementById("mcp-log-clear").addEventListener("click", function() {
    entries.innerHTML = "";
    totalCount = 0;
    countEl.textContent = "0";
    fetch("/api/logs/clear", { method: "POST" });
  });

  var activeFilters = {tools: true, notifications: true, bridge: true, errors: true};
  var levelOrder = ["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"];
  var minLevel = 0;

  // Restore state from URL params
  (function restoreURL() {
    var params = new URLSearchParams(window.location.search);
    if (params.get("log") === "open") {
      panel.classList.remove("hidden");
      openBtn.style.display = "none";
      setFrameLayout("calc(100% - " + panelWidth + "px)");
    }
    var fp = params.get("filters");
    if (fp !== null) {
      var on = fp ? fp.split(",") : [];
      for (var i = 0; i < allFilterKeys.length; i++) {
        var k = allFilterKeys[i];
        activeFilters[k] = on.indexOf(k) !== -1;
        var btn = document.querySelector("[data-filter='" + k + "']");
        if (btn) btn.classList.toggle("active", activeFilters[k]);
      }
    }
    var lp = params.get("level");
    if (lp) {
      var idx = levelOrder.indexOf(lp);
      if (idx >= 0) {
        minLevel = idx;
        document.getElementById("mcp-log-level-select").value = lp;
      }
    }
  })();

  document.getElementById("mcp-log-filters").addEventListener("click", function(e) {
    var btn = e.target.closest("[data-filter]");
    if (!btn) return;
    var f = btn.dataset.filter;
    activeFilters[f] = !activeFilters[f];
    btn.classList.toggle("active", activeFilters[f]);
    applyFilters();
    syncURL();
  });

  document.getElementById("mcp-log-level-select").addEventListener("change", function(e) {
    minLevel = levelOrder.indexOf(e.target.value);
    applyFilters();
    syncURL();
  });

  function shouldShow(el) {
    var cat = el.dataset.category || "";
    if (activeFilters[cat] === false) return false;
    var lv = el.dataset.level;
    if (lv && levelOrder.indexOf(lv) < minLevel) return false;
    return true;
  }

  function applyFilters() {
    var items = entries.querySelectorAll(".log-entry");
    for (var i = 0; i < items.length; i++) {
      items[i].style.display = shouldShow(items[i]) ? "" : "none";
    }
  }

  function summarize(entry) {
    var b = entry.body;
    if (!b) return "";
    if (entry.direction === "request" || entry.direction === "notification") {
      if (b.method === "tools/call" && b.params) return b.params.name || "";
      if (b.method === "resources/read" && b.params) return b.params.uri || "";
      if (b.method === "notifications/message" && b.params) {
        var d = b.params.data;
        if (d && typeof d === "object") return d.msg || d.message || JSON.stringify(d);
        return d || b.params.level || "";
      }
      return "";
    }
    if (b.error) return "error: " + (b.error.message || JSON.stringify(b.error));
    if (b.result && typeof b.result === "object") {
      if (Array.isArray(b.result.tools)) return b.result.tools.length + " tools";
      if (Array.isArray(b.result.resources)) return b.result.resources.length + " resources";
      if (Array.isArray(b.result.prompts)) return b.result.prompts.length + " prompts";
      if (b.result.content) {
        var first = b.result.content[0];
        if (first && first.text) {
          return first.text.length > 60 ? first.text.slice(0, 60) + "\u2026" : first.text;
        }
        return b.result.content.length + " content item(s)";
      }
    }
    return "";
  }

  function formatTime(ts) {
    var d = new Date(ts * 1000);
    return String(d.getHours()).padStart(2, "0") + ":"
      + String(d.getMinutes()).padStart(2, "0") + ":"
      + String(d.getSeconds()).padStart(2, "0");
  }

  function renderEntry(entry) {
    var div = document.createElement("div");
    var isError = entry.direction === "response" && entry.body
      && (entry.body.error || (entry.body.result && entry.body.result.isError));
    div.className = "log-entry" + (isError ? " error" : "");
    var dirClass = isError ? "error" : entry.direction;
    var arrows = {request: "\u2192", response: "\u2190", bridge: "\u2191", notification: "\u2193"};

    // Categorize for filtering
    if (isError) div.dataset.category = "errors";
    else if (entry.direction === "bridge") div.dataset.category = "bridge";
    else if (entry.direction === "notification") div.dataset.category = "notifications";
    else div.dataset.category = "tools";

    var primary = document.createElement("div");
    primary.className = "log-primary";

    var left = document.createElement("div");
    left.className = "log-left";

    var dirEl = document.createElement("span");
    dirEl.className = "log-dir " + dirClass;
    dirEl.textContent = arrows[entry.direction] || "\u2190";

    var methodEl = document.createElement("span");
    methodEl.className = "log-method";
    methodEl.textContent = entry.method || "";

    left.appendChild(dirEl);
    left.appendChild(methodEl);

    // Log level badge for notifications
    if (entry.direction === "notification" && entry.body && entry.body.params) {
      var level = (entry.body.params.level || "").toLowerCase();
      if (level) {
        div.dataset.level = level;
        var lvl = document.createElement("span");
        lvl.className = "log-level log-level-" + level;
        lvl.textContent = level;
        left.appendChild(lvl);
      }
    }

    var metaEl = document.createElement("span");
    metaEl.className = "log-meta";
    metaEl.textContent = entry.duration_ms != null
      ? entry.duration_ms + "ms"
      : formatTime(entry.timestamp);

    var copyBtn = document.createElement("button");
    copyBtn.className = "log-copy";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", function(e) {
      e.stopPropagation();
      navigator.clipboard.writeText(JSON.stringify(entry.body, null, 2));
      copyBtn.textContent = "Copied";
      setTimeout(function() { copyBtn.textContent = "Copy"; }, 1000);
    });

    primary.appendChild(left);
    primary.appendChild(metaEl);
    primary.appendChild(copyBtn);
    div.appendChild(primary);

    var summary = summarize(entry);
    if (summary) {
      var subtitle = document.createElement("div");
      subtitle.className = "log-subtitle";
      subtitle.textContent = summary;
      div.appendChild(subtitle);
    }

    var detail = document.createElement("div");
    detail.className = "log-detail";
    detail.textContent = JSON.stringify(entry.body, null, 2);
    div.appendChild(detail);

    div.addEventListener("click", function() { div.classList.toggle("expanded"); });
    return div;
  }

  var polling = false;
  var firstPoll = true;
  function poll() {
    if (polling) return;
    polling = true;
    fetch("/api/logs?since=" + lastId)
      .then(function(r) { return r.ok ? r.json() : []; })
      .then(function(data) {
        if (!data || !data.length) return;
        lastId = data[data.length - 1].id;
        totalCount += data.length;
        countEl.textContent = String(totalCount);
        var panelVisible = !panel.classList.contains("hidden");
        var atBottom = !panelVisible || entries.scrollHeight - entries.scrollTop - entries.clientHeight < 40;
        for (var i = 0; i < data.length; i++) {
          var el = renderEntry(data[i]);
          el.classList.add("new");
          if (!shouldShow(el)) el.style.display = "none";
          entries.appendChild(el);
        }
        if (atBottom || (firstPoll && panelVisible)) entries.scrollTop = entries.scrollHeight;
        firstPoll = false;
      })
      .catch(function() {})
      .finally(function() { polling = false; });
  }

  window.addEventListener("message", function(event) {
    var data = event.data;
    if (typeof data === "string") {
      try { data = JSON.parse(data); } catch(e) { return; }
    }
    if (!data || typeof data !== "object") return;
    if (!data.jsonrpc && !data.method) return;
    fetch("/api/logs/bridge", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({body: data})
    });
  });

  setInterval(poll, 500);
  poll();
})();
</script>
"""


def _inject_log_panel(html: str) -> str:
    """Inject the MCP message log panel before </body>."""
    return html.replace("</body>", _LOG_PANEL_HTML + "\n</body>")


# ---------------------------------------------------------------------------
# Picker UI (Prefab-based, built in Python)
# ---------------------------------------------------------------------------


def _has_ui_resource(tool: dict[str, Any]) -> bool:
    """Return True if the tool has a UI resourceUri in its metadata."""
    for key in ("meta", "_meta"):
        m = tool.get(key)
        if isinstance(m, dict):
            ui = m.get("ui")
            if isinstance(ui, dict) and ui.get("resourceUri"):
                return True
    return False


def _model_from_schema(tool_name: str, input_schema: dict[str, Any]) -> type[Any]:
    """Dynamically create a Pydantic model from a JSON Schema for form generation."""
    import pydantic
    import pydantic.fields

    properties: dict[str, Any] = input_schema.get("properties") or {}
    required: list[str] = input_schema.get("required") or []

    field_definitions: dict[str, Any] = {}
    for prop_name, prop in properties.items():
        json_type = prop.get("type", "string")

        # Handle anyOf / oneOf (union types like str | dict | None)
        for key in ("anyOf", "oneOf"):
            if key in prop:
                non_null = [
                    t
                    for t in prop[key]
                    if isinstance(t, dict) and t.get("type") != "null"
                ]
                if non_null:
                    types = [t.get("type") for t in non_null if "type" in t]
                    # Prefer object/array (need textarea for JSON editing),
                    # then string (most versatile text input), then scalars.
                    for candidate in (
                        "object",
                        "array",
                        "string",
                        "integer",
                        "number",
                        "boolean",
                    ):
                        if candidate in types:
                            json_type = candidate
                            break
                break

        match json_type:
            case "integer":
                py_type: type = int
            case "number":
                py_type = float
            case "boolean":
                py_type = bool
            case "object" | "array":
                # Render as a string textarea; api_launch parses JSON later
                py_type = str
            case _:
                py_type = str

        title = prop.get("title") or prop_name.replace("_", " ").title()
        description = prop.get("description")
        is_required = prop_name in required
        if is_required:
            default = pydantic.fields.PydanticUndefined
        elif "default" in prop:
            default = prop["default"]
        else:
            default = None
            py_type = py_type | None  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

        extra: dict[str, Any] = {}
        if prop.get("enum"):
            from typing import Literal

            py_type = Literal[tuple(prop["enum"])]  # type: ignore[assignment]  # ty:ignore[invalid-type-form]

        # Textarea detection:
        # 1. Explicit format: "textarea" in JSON schema
        # 2. UI annotation: {"ui": {"type": "textarea"}} (json_schema_extra merged flat)
        # 3. Object/array types need multiline JSON editing
        use_textarea = (
            prop.get("format") == "textarea"
            or (
                isinstance(prop.get("ui"), dict)
                and prop["ui"].get("type") == "textarea"
            )
            or json_type in ("object", "array")
        )
        if use_textarea:
            extra["json_schema_extra"] = {"ui": {"type": "textarea"}}

        field_definitions[prop_name] = (
            py_type,
            pydantic.Field(
                default=default, title=title, description=description, **extra
            ),
        )

    return pydantic.create_model(f"{tool_name.title()}Form", **field_definitions)


def _build_picker_html(tools: list[dict[str, Any]]) -> str:
    """Build Prefab picker page: dropdown selector with per-tool forms."""
    try:
        from prefab_ui.actions import Fetch, OpenLink, SetState, ShowToast
        from prefab_ui.app import PrefabApp
        from prefab_ui.components import (
            Button,
            Column,
            Heading,
            Label,
            Markdown,
            Muted,
            Page,
            Pages,
            Select,
            SelectOption,
            Textarea,
        )
        from prefab_ui.components.form import Form
        from prefab_ui.rx import RESULT, Rx
    except ImportError:
        return "<html><body><p>prefab-ui not installed. Run: pip install fastmcp[apps]</p></body></html>"

    if not tools:
        with Column(gap=4, css_class="p-6 max-w-2xl mx-auto") as view:
            Heading("FastMCP Apps")
            Muted(
                "No UI tools found on this server. Use @app.ui() to register entry-point tools."
            )
        return PrefabApp(title="FastMCP Apps", view=view).html()

    first_name: str = tools[0]["name"]

    def _tool_title(tool: dict[str, Any]) -> str:
        return tool.get("title") or tool["name"]

    with Column(gap=6, css_class="p-8 max-w-2xl mx-auto") as view:
        Heading("FastMCP Apps")

        if len(tools) > 1:
            with Column(gap=1):
                Label("Tool")
                with Select(
                    placeholder="Choose a tool…",
                    on_change=SetState("activeTool", Rx("$event")),
                ):
                    for tool in tools:
                        SelectOption(
                            _tool_title(tool),
                            value=tool["name"],
                            selected=tool["name"] == first_name,
                        )
        else:
            Heading(_tool_title(tools[0]), level=3)

        with Pages(name="activeTool", value=first_name):
            for tool in tools:
                name: str = tool["name"]
                desc: str = tool.get("description") or ""
                input_schema: dict[str, Any] = tool.get("inputSchema") or {}
                model = _model_from_schema(name, input_schema)

                form_body: dict[str, Any] = {"tool": name}
                for field_name in model.model_fields:
                    form_body[field_name] = Rx(field_name)

                json_body: dict[str, Any] = {
                    "tool": name,
                    "__json_args__": Rx("__json_args__"),
                }

                on_error = ShowToast(Rx("$error"), variant="error")  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

                input_mode = f"_mode_{name}"
                _desc_max_lines = 10
                with Page(name, value=name), Column(gap=4):
                    if desc:
                        lines = desc.split("\n")
                        md_css = "text-sm text-muted-foreground"
                        if len(lines) <= _desc_max_lines:
                            Markdown(desc, css_class=md_css)
                        else:
                            desc_state = f"_desc_{name}"
                            short = "\n".join(lines[:_desc_max_lines])
                            with Pages(name=desc_state, value="short"):
                                with (
                                    Page("short", value="short"),
                                    Column(gap=1, css_class="items-start"),
                                ):
                                    Markdown(short, css_class=md_css)
                                    Button(
                                        "Show more \u25be",
                                        variant="link",
                                        size="xs",
                                        on_click=SetState(desc_state, "full"),
                                        css_class="text-muted-foreground p-0 h-auto",
                                    )
                                with (
                                    Page("full", value="full"),
                                    Column(gap=1, css_class="items-start"),
                                ):
                                    Markdown(desc, css_class=md_css)
                                    Button(
                                        "Show less \u25b4",
                                        variant="link",
                                        size="xs",
                                        on_click=SetState(desc_state, "short"),
                                        css_class="text-muted-foreground p-0 h-auto",
                                    )

                    with Pages(name=input_mode, value="form"):
                        with Page("form", value="form"), Column(gap=4):
                            with Column(gap=1, css_class="items-start"):
                                Heading("Arguments", level=3)
                                Button(
                                    "Edit as JSON",
                                    variant="link",
                                    size="xs",
                                    on_click=SetState(input_mode, "json"),
                                    css_class="text-muted-foreground p-0 h-auto",
                                )
                            with Form(
                                on_submit=Fetch.post(
                                    "/api/launch",
                                    body=form_body,
                                    on_success=OpenLink(RESULT),
                                    on_error=on_error,
                                ),
                            ):
                                Form.from_model(model, fields_only=True)
                                Button(
                                    "Launch",
                                    variant="success",
                                    button_type="submit",
                                )
                        with Page("json", value="json"), Column(gap=4):
                            with Column(gap=1, css_class="items-start"):
                                Heading("Arguments", level=3)
                                Button(
                                    "Use form",
                                    variant="link",
                                    size="xs",
                                    on_click=SetState(input_mode, "form"),
                                    css_class="text-muted-foreground p-0 h-auto",
                                )
                            with Form(
                                on_submit=Fetch.post(
                                    "/api/launch",
                                    body=json_body,
                                    on_success=OpenLink(RESULT),
                                    on_error=on_error,
                                ),
                            ):
                                Textarea(
                                    name="__json_args__",
                                    placeholder='{"key": "value"}',
                                    rows=8,
                                )
                                Button(
                                    "Launch",
                                    variant="success",
                                    button_type="submit",
                                )

        Markdown(
            "Generated by [Prefab](https://prefab.prefect.io) 🎨",
            css_class="text-xs text-muted-foreground text-right",
        )

    return PrefabApp(title="FastMCP Apps", view=view).html()


# ---------------------------------------------------------------------------
# MCP tool listing helper
# ---------------------------------------------------------------------------


async def _list_tools(mcp_url: str) -> list[dict[str, Any]]:
    """Return raw tool dicts from the MCP server at mcp_url."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        return []

    try:
        async with streamable_http_client(mcp_url) as (read, write, _):  # noqa: SIM117
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [t.model_dump() for t in result.tools]
    except Exception as exc:
        logger.debug(f"Could not list tools from {mcp_url}: {exc}")
        return []


async def _read_mcp_resource(mcp_url: str, uri: str) -> str | None:
    """Read an MCP resource by URI and return its text content."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from pydantic import AnyUrl
    except ImportError:
        return None

    try:
        async with streamable_http_client(mcp_url) as (read, write, _):  # noqa: SIM117
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.read_resource(AnyUrl(uri))
                for content in result.contents:
                    text = getattr(content, "text", None)
                    if text:
                        return text
        return None
    except Exception as exc:
        logger.debug(f"Could not read resource {uri} from {mcp_url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# app-bridge.js download, patch, and Zod import-map generation
# ---------------------------------------------------------------------------


def _fetch_app_bridge_bundle_sync(
    version: str,
    sdk_version: str,
) -> tuple[str, str]:
    """Download app-bridge.js and build an import-map that fixes Zod v4 on esm.sh.

    Returns ``(app_bridge_js, import_map_json)`` where *import_map_json* is a
    JSON string ready to embed in a ``<script type="importmap">`` tag.

    Background
    ----------
    esm.sh's ``zod@x.y.z/es2022/v4.mjs`` only re-exports ``{z, default}``,
    losing all individual named exports (``custom``, ``string``, etc.).  The
    MCP SDK does ``import * as t from "zod/v4"`` and calls ``t.custom(…)``
    which fails.  ``zod@x.y.z/es2022/v4/classic/index.mjs`` exports everything
    correctly.  An import-map that remaps the broken URL to the working one
    fixes all modules in the page's graph, including those loaded cross-origin
    from esm.sh.

    ext-apps app-bridge.js imports the SDK via bare specifiers
    (``@modelcontextprotocol/sdk/types.js`` etc.) that the browser cannot
    resolve.  We rewrite them to concrete esm.sh URLs before serving.
    """
    cache_path = (
        Path(tempfile.gettempdir())
        / f"fastmcp-ext-apps-{version}-sdk-{sdk_version}-bundle.json"
    )
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        return cached["app_bridge_js"], cached["import_map_json"]

    # -- Download and patch app-bridge.js -----------------------------------
    npm_url = f"https://registry.npmjs.org/@modelcontextprotocol/ext-apps/-/ext-apps-{version}.tgz"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(npm_url, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.extractfile("package/dist/src/app-bridge.js")
        if member is None:
            raise RuntimeError("app-bridge.js not found in ext-apps tarball")
        app_bridge_js = member.read().decode()

    # Rewrite bare SDK module specifiers to concrete esm.sh URLs
    sdk_base = f"https://esm.sh/@modelcontextprotocol/sdk@{sdk_version}"
    for sdk_path in ("types.js", "shared/protocol.js"):
        app_bridge_js = app_bridge_js.replace(
            f'from"@modelcontextprotocol/sdk/{sdk_path}"',
            f'from"{sdk_base}/{sdk_path}"',
        )

    # -- Detect the broken Zod v4.mjs URL -----------------------------------
    # The SDK's types module imports zod/v4 via a version-range URL like
    # /zod@^4.3.5/v4?target=es2022.  That wrapper re-exports from the
    # version-specific v4.mjs (e.g. /zod@4.3.6/es2022/v4.mjs) which is
    # broken.  We fetch the wrapper to discover the exact version.
    types_url = f"{sdk_base}/types.js"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(types_url, follow_redirects=True)
        resp.raise_for_status()
        types_content = resp.text

    # Extract the zod/v4?target=es2022 path from the types.js redirect
    zod_wrapper_match = re.search(r'import "(/zod@[^"]*v4[^"]*)"', types_content)
    if not zod_wrapper_match:
        raise RuntimeError(
            f"Could not find zod/v4 import in {types_url}:\n{types_content[:500]}"
        )
    zod_wrapper_path = zod_wrapper_match.group(1)  # e.g. /zod@^4.3.5/v4?target=es2022

    zod_wrapper_url = f"https://esm.sh{zod_wrapper_path}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(zod_wrapper_url, follow_redirects=True)
        resp.raise_for_status()
        wrapper_content = resp.text

    # The wrapper does: export * from "/zod@4.3.6/es2022/v4.mjs"
    broken_match = re.search(
        r'export \* from "(/zod@[\d.]+/es2022/v4\.mjs)"', wrapper_content
    )
    if not broken_match:
        raise RuntimeError(
            f"Could not find v4.mjs re-export in {zod_wrapper_url}:\n{wrapper_content[:500]}"
        )
    broken_path = broken_match.group(1)  # e.g. /zod@4.3.6/es2022/v4.mjs
    zod_version = broken_path.split("@")[1].split("/")[0]  # e.g. 4.3.6

    broken_url = f"https://esm.sh{broken_path}"
    fixed_url = f"https://esm.sh/zod@{zod_version}/es2022/v4/classic/index.mjs"

    import_map_json = json.dumps({"imports": {broken_url: fixed_url}})

    # -- Cache and return ----------------------------------------------------
    cache_path.write_text(
        json.dumps({"app_bridge_js": app_bridge_js, "import_map_json": import_map_json})
    )
    return app_bridge_js, import_map_json


async def _fetch_app_bridge_bundle(
    version: str,
    sdk_version: str,
) -> tuple[str, str]:
    """Async wrapper around _fetch_app_bridge_bundle_sync."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _fetch_app_bridge_bundle_sync, version, sdk_version
    )


# ---------------------------------------------------------------------------
# FastAPI dev server
# ---------------------------------------------------------------------------


def _make_dev_app(
    mcp_url: str,
    app_bridge_js: str,
    import_map_tag: str,
    message_log: _MessageLog,
) -> Starlette:
    """Build the Starlette dev server application."""

    async def picker(request: Request) -> HTMLResponse:
        """AppBridge host page — loads the picker app in an iframe and wires the bridge."""
        host_html = _HOST_SHELL.format(
            title="FastMCP Apps",
            import_map_tag=import_map_tag,
            status_text="",
            status_display="none",
            frame_display="block",
            mcp_sdk_version=_MCP_SDK_VERSION,
            iframe_src_json=json.dumps("/picker-app"),
            on_open_link="bridge.onopenlink = async ({ url }) => { window.location.href = url; return {}; };",
            on_initialized="bridge.oninitialized = async () => {};",
        )
        return HTMLResponse(_inject_log_panel(host_html))

    async def picker_app(request: Request) -> HTMLResponse:
        """Prefab picker UI — tool list with one tab per UI tool."""
        try:
            raw_tools = await _list_tools(mcp_url)
            ui_tools = [t for t in raw_tools if _has_ui_resource(t)]
            html = _build_picker_html(ui_tools)
        except Exception as exc:
            logger.exception("Error building picker UI")
            html = f"<pre style='padding:2rem;color:red'>Error: {exc}</pre>"
        return HTMLResponse(html)

    async def launch(request: Request) -> HTMLResponse:
        """Host page: GET /launch?tool=name&args={...}"""
        tool = request.query_params.get("tool", "")
        args_raw = request.query_params.get("args", "{}")
        tool_args = json.loads(args_raw)
        host_html = _HOST_HTML_TEMPLATE.format(
            tool_name=tool,
            import_map_tag=import_map_tag,
            tool_name_json=json.dumps(tool),
            tool_args_json=json.dumps(tool_args),
            mcp_sdk_version=_MCP_SDK_VERSION,
        )
        return HTMLResponse(_inject_log_panel(host_html))

    async def api_launch(request: Request) -> Response:
        """Picker form submits here; returns a /launch URL string for OpenLink."""
        data = await request.json()
        tool = data.pop("tool", "")

        # JSON mode: the entire argument dict arrives as a raw JSON string.
        # Key uses a dunder prefix to avoid collisions with real tool params.
        raw_json_args = data.pop("__json_args__", None)
        if raw_json_args is not None:
            if not raw_json_args.strip():
                tool_args = {}
            else:
                try:
                    tool_args = json.loads(raw_json_args)
                except json.JSONDecodeError as exc:
                    return Response(
                        content=json.dumps({"error": f"Invalid JSON: {exc}"}),
                        status_code=400,
                        media_type="application/json",
                    )
                if not isinstance(tool_args, dict):
                    return Response(
                        content=json.dumps(
                            {
                                "error": "JSON must be an object, not "
                                + type(tool_args).__name__
                            }
                        ),
                        status_code=400,
                        media_type="application/json",
                    )
        else:
            # Form mode: inputs are always strings — try to parse values
            # that look like JSON objects or arrays.
            tool_args = {}
            for k, v in data.items():
                if isinstance(v, str):
                    stripped = v.strip()
                    # Skip empty strings — the form sends them for
                    # unfilled optional fields, but they'll fail
                    # validation against non-string types.
                    if not stripped:
                        continue
                    if stripped[0] in ("{", "["):
                        try:
                            parsed = json.loads(stripped)
                            if isinstance(parsed, (dict, list)):
                                v = parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                tool_args[k] = v
        args_json = quote(json.dumps(tool_args))
        url = f"/launch?tool={tool}&args={args_json}"
        return Response(
            content=json.dumps(url),
            media_type="application/json",
        )

    async def ui_resource(request: Request) -> Response:
        """Fetch an MCP resource server-side and return it as HTML.

        Used by the launch page to load the renderer via iframe.src rather
        than iframe.srcdoc — avoids a race condition where the Prefab renderer
        sends its MCP initialize message before the AppBridge transport is
        listening (srcdoc parses and runs module scripts synchronously, while
        iframe.src load adds the network-roundtrip gap needed).
        """
        uri = request.query_params.get("uri", "")
        if not uri:
            return Response("Missing uri parameter", status_code=400)
        html = await _read_mcp_resource(mcp_url, uri)
        if html is None:
            return Response(f"Could not read MCP resource: {uri}", status_code=502)
        return HTMLResponse(html)

    async def serve_app_bridge_js(request: Request) -> Response:
        """Serve the locally patched app-bridge.js."""
        return Response(
            content=app_bridge_js,
            media_type="application/javascript",
        )

    async def proxy_mcp(request: Request) -> Response:
        """Proxy all MCP requests to the user's server (avoids browser CORS)."""
        body = await request.body()

        # Log MCP requests
        if body and request.method == "POST":
            try:
                req_json = json.loads(body)
                if isinstance(req_json, list):
                    for item in req_json:
                        if isinstance(item, dict):
                            message_log.log_request(item)
                elif isinstance(req_json, dict):
                    message_log.log_request(req_json)
            except (json.JSONDecodeError, TypeError):
                pass

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        # Use a reasonable default timeout to prevent the proxy from hanging
        # if the backend server is unresponsive.
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))

        async def _stream_and_cleanup(resp: httpx.Response) -> Any:
            is_sse = "text/event-stream" in resp.headers.get("content-type", "")
            buf: list[bytes] = []
            sse_buf = ""
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
                    if is_sse:
                        # Parse SSE events incrementally
                        sse_buf += chunk.decode("utf-8", errors="replace")
                        while "\r\n\r\n" in sse_buf or "\n\n" in sse_buf:
                            # Split on whichever double-newline appears first
                            ri = sse_buf.find("\r\n\r\n")
                            ni = sse_buf.find("\n\n")
                            if ri >= 0 and (ni < 0 or ri < ni):
                                event, sse_buf = sse_buf[:ri], sse_buf[ri + 4 :]
                            else:
                                event, sse_buf = sse_buf[:ni], sse_buf[ni + 2 :]
                            for line in event.splitlines():
                                if line.startswith("data: "):
                                    with contextlib.suppress(json.JSONDecodeError):
                                        message_log.log_response(json.loads(line[6:]))
                    else:
                        buf.append(chunk)
            except (
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.ReadTimeout,  
                httpcore.RemoteProtocolError,
            ):
                pass  # Connection closed during shutdown — not an error
            finally:
                # Log non-SSE responses (JSON) after stream completes
                if buf:
                    _log_response_bytes(message_log, b"".join(buf), "application/json")
                with contextlib.suppress(Exception):
                    await resp.aclose()
                with contextlib.suppress(Exception):
                    await client.aclose()

        try:
            req = client.build_request(
                method=request.method,
                url=mcp_url,
                content=body,
                headers=headers,
                params=dict(request.query_params),
            )
            resp = await client.send(req, stream=True)
            content_type = resp.headers.get("content-type", "")
            # Strip hop-by-hop headers that shouldn't be forwarded
            fwd_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower()
                not in (
                    "transfer-encoding",
                    "connection",
                    "keep-alive",
                    "content-encoding",
                )
            }
            return StreamingResponse(
                _stream_and_cleanup(resp),
                status_code=resp.status_code,
                headers=fwd_headers,
                media_type=content_type or "application/octet-stream",
            )
        except httpx.ConnectError:
            await client.aclose()
            return Response(
                content=json.dumps({"error": "MCP server not reachable"}).encode(),
                status_code=503,
                media_type="application/json",
            )

    async def api_logs(request: Request) -> Response:
        """Return message log entries since a given id."""
        since = int(request.query_params.get("since", "0"))
        entries = message_log.get_since(since)
        return Response(
            content=json.dumps(entries),
            media_type="application/json",
        )

    async def api_logs_bridge(request: Request) -> Response:
        """Log a bridge message (postMessage between app iframe and host)."""
        data = await request.json()
        message_log.log_bridge(data.get("body", data))
        return Response(content="{}", media_type="application/json")

    async def api_logs_clear(request: Request) -> Response:
        """Clear the message log."""
        message_log.clear()
        return Response(content="{}", media_type="application/json")

    return Starlette(
        routes=[
            Route("/", picker),
            Route("/picker-app", picker_app),
            Route("/launch", launch),
            Route("/api/launch", api_launch, methods=["POST"]),
            Route("/api/logs", api_logs),
            Route("/api/logs/bridge", api_logs_bridge, methods=["POST"]),
            Route("/api/logs/clear", api_logs_clear, methods=["POST"]),
            Route("/ui-resource", ui_resource),
            Route("/js/app-bridge.js", serve_app_bridge_js),
            Route(
                "/mcp",
                proxy_mcp,
                methods=["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS"],
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Launch helpers
# ---------------------------------------------------------------------------


async def _start_user_server(
    server_spec: str,
    mcp_port: int,
    *,
    reload: bool = True,
) -> asyncio.subprocess.Process:
    """Start the user's MCP server as a subprocess on mcp_port."""
    cmd = [
        sys.executable,
        "-m",
        "fastmcp.cli",
        "run",
        server_spec,
        "--transport",
        "http",
        "--port",
        str(mcp_port),
        "--no-banner",
    ]
    if reload:
        cmd.append("--reload")
    else:
        cmd.append("--no-reload")
    env = {**os.environ, "FASTMCP_LOG_LEVEL": "WARNING"}
    process = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        start_new_session=sys.platform != "win32",
    )
    return process


async def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll until the server is accepting connections."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with httpx.AsyncClient() as client:
        while loop.time() < deadline:
            try:
                await client.get(url, timeout=1.0)
                return True
            except (
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.TimeoutException,
            ):
                await asyncio.sleep(0.25)
    return False


async def run_dev_apps(
    server_spec: str,
    *,
    mcp_port: int = 8000,
    dev_port: int = 8080,
    reload: bool = True,
) -> None:
    """Start the full dev environment for a FastMCPApp server.

    Starts the user's MCP server on *mcp_port*, starts the Prefab dev UI
    on *dev_port* (with an /mcp proxy to the user's server), then opens
    the browser.
    """
    mcp_url = f"http://localhost:{mcp_port}/mcp"
    dev_url = f"http://localhost:{dev_port}"

    user_proc: asyncio.subprocess.Process | None = None

    async def _body() -> None:
        nonlocal user_proc

        # Check ports before starting anything
        import socket

        for port, label, flag in [
            (mcp_port, "MCP server", "--mcp-port"),
            (dev_port, "dev UI", "--dev-port"),
        ]:
            in_use = False
            for family, addr in (
                (socket.AF_INET, ("127.0.0.1", port)),
                (socket.AF_INET6, ("::1", port, 0, 0)),
            ):
                try:
                    with socket.socket(family, socket.SOCK_STREAM) as s:
                        if s.connect_ex(addr) == 0:
                            in_use = True
                            break
                except OSError:
                    continue
            if in_use:
                logger.error(
                    f"Port {port} ({label}) is already in use. "
                    f"Try {flag} to use a different port."
                )
                sys.exit(1)

        logger.info(f"Starting user server on port {mcp_port}…")
        logger.info("Fetching app-bridge.js from npm…")

        # Start the server first so user_proc is assigned before anything
        # that might fail (e.g. npm fetch).  This ensures the finally
        # cleanup can kill the subprocess even if the bundle fetch raises.
        user_proc = await _start_user_server(server_spec, mcp_port, reload=reload)
        app_bridge_js, import_map_json = await _fetch_app_bridge_bundle(
            _EXT_APPS_VERSION, _MCP_SDK_VERSION
        )

        import_map_tag = (
            f'  <script type="importmap">\n  {import_map_json}\n  </script>'
        )

        ready = await _wait_for_server(mcp_url, timeout=15.0)
        if not ready:
            raise RuntimeError(f"User server did not start on port {mcp_port}")

        logger.info(f"FastMCP dev UI at {dev_url}")

        dev_app = _make_dev_app(mcp_url, app_bridge_js, import_map_tag, _MessageLog())
        config = uvicorn.Config(
            dev_app,
            host="localhost",
            port=dev_port,
            log_level="warning",
            ws="websockets-sansio",
        )
        server = uvicorn.Server(config)
        # Suppress uvicorn's own signal handlers — they use signal.signal() which
        # conflicts with asyncio and causes hangs.  We cancel the task instead.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]  # ty:ignore[unresolved-attribute]

        async def _open_browser() -> None:
            await asyncio.sleep(0.8)
            webbrowser.open(dev_url)

        await asyncio.gather(server.serve(), _open_browser())

    # Register signal handlers before any work starts so that Ctrl+C during
    # startup (server spawn, npm fetch, server-ready poll) is handled the same
    # way as Ctrl+C during the running phase — both cancel the body task and
    # fall through to the cleanup finally block.
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(_body())

    def _on_signal() -> None:
        # Silence uvicorn's error logger before cancelling so that the
        # CancelledError propagating through uvicorn doesn't get logged as
        # an ERROR during the forced shutdown.
        logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
        task.cancel()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)

    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if sys.platform != "win32":
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
        if user_proc is not None and user_proc.returncode is None:
            # Kill the entire process group (not just the top-level process)
            # because --reload creates a watcher that spawns child processes.
            # Killing only the watcher leaves the actual server holding the port.
            try:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(user_proc.pid), signal.SIGTERM)
                else:
                    user_proc.kill()
            except (ProcessLookupError, PermissionError):
                user_proc.kill()
            await user_proc.wait()
