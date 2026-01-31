"""QR Code MCP App Server — generates QR codes with an interactive view UI.

Demonstrates MCP Apps with FastMCP:
- Tool linked to a ui:// resource via ToolUI
- HTML resource with CSP metadata for CDN-loaded dependencies
- Embedded HTML using the @modelcontextprotocol/ext-apps JS SDK
- ImageContent return type for binary data
- Both stdio and HTTP transport modes

Based on https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/qr-server

Setup (from examples/apps/):
    uv sync

Usage:
    uv run python qr_server.py            # HTTP mode (port 3001)
    uv run python qr_server.py --stdio     # stdio mode for MCP clients
"""

from __future__ import annotations

import base64
import io

import qrcode  # type: ignore[import-untyped]
from mcp import types

from fastmcp import FastMCP
from fastmcp.server.apps import ResourceCSP, ResourceUI, ToolUI
from fastmcp.tools import ToolResult

VIEW_URI: str = "ui://qr-server/view.html"

mcp: FastMCP = FastMCP("QR Code Server")

EMBEDDED_VIEW_HTML: str = """\
<!DOCTYPE html>
<html>
<head>
  <meta name="color-scheme" content="light dark">
  <style>
    html, body {
      margin: 0;
      padding: 0;
      overflow: hidden;
      background: transparent;
    }
    body {
      display: flex;
      justify-content: center;
      align-items: center;
      height: 340px;
      width: 340px;
    }
    img {
      width: 300px;
      height: 300px;
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
  </style>
</head>
<body>
  <div id="qr"></div>
  <script type="module">
    import { App } from "https://unpkg.com/@modelcontextprotocol/ext-apps@0.4.0/app-with-deps";

    const app = new App({ name: "QR View", version: "1.0.0" });

    app.ontoolresult = ({ content }) => {
      const img = content?.find(c => c.type === 'image');
      if (img) {
        const qrDiv = document.getElementById('qr');
        qrDiv.innerHTML = '';

        const allowedTypes = ['image/png', 'image/jpeg', 'image/gif'];
        const mimeType = allowedTypes.includes(img.mimeType) ? img.mimeType : 'image/png';

        const image = document.createElement('img');
        image.src = `data:${mimeType};base64,${img.data}`;
        image.alt = "QR Code";
        qrDiv.appendChild(image);
      }
    };

    function handleHostContextChanged(ctx) {
      if (ctx.safeAreaInsets) {
        document.body.style.paddingTop = `${ctx.safeAreaInsets.top}px`;
        document.body.style.paddingRight = `${ctx.safeAreaInsets.right}px`;
        document.body.style.paddingBottom = `${ctx.safeAreaInsets.bottom}px`;
        document.body.style.paddingLeft = `${ctx.safeAreaInsets.left}px`;
      }
    }

    app.onhostcontextchanged = handleHostContextChanged;

    await app.connect();
    const ctx = app.getHostContext();
    if (ctx) {
      handleHostContextChanged(ctx);
    }
  </script>
</body>
</html>"""


@mcp.tool(ui=ToolUI(resource_uri=VIEW_URI))
def generate_qr(
    text: str = "https://gofastmcp.com",
    box_size: int = 10,
    border: int = 4,
    error_correction: str = "M",
    fill_color: str = "black",
    back_color: str = "white",
) -> ToolResult:
    """Generate a QR code from text.

    Args:
        text: The text/URL to encode
        box_size: Size of each box in pixels (default: 10)
        border: Border size in boxes (default: 4)
        error_correction: Error correction level - L(7%), M(15%), Q(25%), H(30%)
        fill_color: Foreground color (hex like #FF0000 or name like red)
        back_color: Background color (hex like #FFFFFF or name like white)
    """
    error_levels = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }

    if box_size <= 0:
        raise ValueError("box_size must be > 0")
    if border < 0:
        raise ValueError("border must be >= 0")

    error_key = error_correction.upper()
    if error_key not in error_levels:
        raise ValueError(f"error_correction must be one of: {', '.join(error_levels)}")

    qr = qrcode.QRCode(
        version=1,
        error_correction=error_levels[error_key],
        box_size=box_size,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(fill_color=fill_color, back_color=back_color)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return ToolResult(
        content=[types.ImageContent(type="image", data=b64, mimeType="image/png")]
    )


@mcp.resource(
    VIEW_URI,
    ui=ResourceUI(csp=ResourceCSP(resource_domains=["https://unpkg.com"])),
)
def view() -> str:
    """Interactive QR code viewer — renders tool results as images."""
    return EMBEDDED_VIEW_HTML


if __name__ == "__main__":
    mcp.run()
