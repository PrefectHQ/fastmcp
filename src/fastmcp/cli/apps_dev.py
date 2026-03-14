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
        match json_type:
            case "integer":
                py_type: type = int
            case "number":
                py_type = float
            case "boolean":
                py_type = bool
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
            py_type = py_type | None  # type: ignore[assignment]

        extra: dict[str, Any] = {}
        if prop.get("enum"):
            from typing import Literal

            py_type = Literal[tuple(prop["enum"])]  # type: ignore[assignment]
        if prop.get("format") == "textarea" or (
            isinstance(prop.get("json_schema_extra"), dict)
            and prop["json_schema_extra"].get("ui", {}).get("type") == "textarea"
        ):
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
        )
        from prefab_ui.components.form import Form
        from prefab_ui.rx import RESULT, Rx
    except ImportError:
        return "<html><body><p>prefab-ui not installed. Run: pip install fastmcp[apps]</p></body></html>"

    if not tools:
        with Column(gap=4, css_class="p-6 max-w-2xl mx-auto") as view:
            Heading("FastMCP App Preview")
            Muted(
                "No UI tools found on this server. Use @app.ui() to register entry-point tools."
            )
        return PrefabApp(title="FastMCP App Preview", view=view).html()

    first_name: str = tools[0]["name"]

    def _tool_title(tool: dict[str, Any]) -> str:
        return tool.get("title") or tool["name"]

    with Column(gap=6, css_class="p-8 max-w-lg mx-auto") as view:
        Heading("FastMCP App Preview")

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

        with Pages(name="activeTool", default_value=first_name):
            for tool in tools:
                name: str = tool["name"]
                desc: str = tool.get("description") or ""
                input_schema: dict[str, Any] = tool.get("inputSchema") or {}
                model = _model_from_schema(name, input_schema)

                body: dict[str, Any] = {"tool": name}
                for field_name in model.model_fields:
                    body[field_name] = Rx(field_name)

                with Page(name, value=name), Column(gap=4):
                    if desc:
                        Muted(desc, css_class="pb-2")
                    with Form(
                        on_submit=Fetch.post(
                            "/api/launch",
                            body=body,
                            on_success=OpenLink(RESULT),
                            on_error=ShowToast(Rx("$error"), variant="error"),  # type: ignore[arg-type]
                        ),
                    ):
                        Form.from_model(model, fields_only=True)
                        Button(
                            "Launch",
                            variant="success",
                            button_type="submit",
                        )

        Markdown(
            "Generated by [Prefab](https://prefab.prefect.io) 🎨",
            css_class="text-xs text-muted-foreground text-right",
        )

    return PrefabApp(title="FastMCP App Preview", view=view).html()


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
    with urllib.request.urlopen(npm_url) as resp:
        data = resp.read()

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
    with urllib.request.urlopen(types_url) as resp:
        types_content = resp.read().decode()

    # Extract the zod/v4?target=es2022 path from the types.js redirect
    zod_wrapper_match = re.search(r'import "(/zod@[^"]*v4[^"]*)"', types_content)
    if not zod_wrapper_match:
        raise RuntimeError(
            f"Could not find zod/v4 import in {types_url}:\n{types_content[:500]}"
        )
    zod_wrapper_path = zod_wrapper_match.group(1)  # e.g. /zod@^4.3.5/v4?target=es2022

    zod_wrapper_url = f"https://esm.sh{zod_wrapper_path}"
    with urllib.request.urlopen(zod_wrapper_url) as resp:
        wrapper_content = resp.read().decode()

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
) -> Starlette:
    """Build the Starlette dev server application."""

    async def picker(request: Request) -> HTMLResponse:
        """AppBridge host page — loads the picker app in an iframe and wires the bridge."""
        host_html = _HOST_SHELL.format(
            title="FastMCP App Preview",
            import_map_tag=import_map_tag,
            status_text="",
            status_display="none",
            frame_display="block",
            mcp_sdk_version=_MCP_SDK_VERSION,
            iframe_src_json=json.dumps("/picker-app"),
            on_open_link="bridge.onopenlink = async ({ url }) => { window.location.href = url; return {}; };",
            on_initialized="bridge.oninitialized = async () => {};",
        )
        return HTMLResponse(host_html)

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
        return HTMLResponse(host_html)

    async def api_launch(request: Request) -> Response:
        """Picker form submits here; returns a /launch URL string for OpenLink."""
        data = await request.json()
        tool = data.pop("tool", "")
        # Remaining keys are tool arguments; pass all including empty optionals
        tool_args = dict(data)
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
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        client = httpx.AsyncClient(timeout=None)

        async def _stream_and_cleanup(resp: httpx.Response) -> Any:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except (
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpcore.RemoteProtocolError,
            ):
                pass  # Connection closed during shutdown — not an error
            finally:
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

    return Starlette(
        routes=[
            Route("/", picker),
            Route("/picker-app", picker_app),
            Route("/launch", launch),
            Route("/api/launch", api_launch, methods=["POST"]),
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

        dev_app = _make_dev_app(mcp_url, app_bridge_js, import_map_tag)
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
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

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
