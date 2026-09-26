# Media Picker MCP App

An interactive, source-agnostic media picker built with `FastMCPApp` and Prefab.
It demonstrates a decoupled flow:

1. `discover_media` returns normalized candidates that the model can reason about.
2. `show_media_picker` renders playable candidate IDs as a compact in-conversation list.
3. `Play` and `Save` call app-only tools without another model turn.
4. `More like this` sends a focused follow-up request back to the conversation.

`discover_media` searches a static sample catalog, not YouTube or the web. `Save`
stores IDs in memory until the server restarts. Demo playback returns a receipt
without controlling hardware. To control a
real device, point it at an MCP server with a playback tool that accepts `source`,
`source_id`, `url`, and `title`:

```bash
export MEDIA_PICKER_ACTUATOR_URL=http://127.0.0.1:8764/mcp
export MEDIA_PICKER_ACTUATOR_TOOL=play_media
export MEDIA_PICKER_ACTUATOR_SOURCES=youtube
```

The [smart-home example](../../smart_home/README.md) documents how to start the
standalone Fire TV endpoint used above. If you expose its combined hub instead,
set `MEDIA_PICKER_ACTUATOR_TOOL=fire_tv_play_media`. When an
actuator is configured, `MEDIA_PICKER_ACTUATOR_SOURCES` is required: the picker hides
unsupported results and direct calls fail before dispatch. This prevents a source URL
from reaching an actuator that cannot interpret it.

## Run

From the repository root:

```bash
uv run python examples/apps/media_picker/media_picker_server.py
```

The streamable HTTP endpoint is available at `http://localhost:8000/mcp`.

For local UI development:

```bash
uv run fastmcp dev apps examples/apps/media_picker/media_picker_server.py
```

In ChatGPT developer mode, expose port 8000 through a public HTTPS tunnel, connect the
resulting `/mcp` URL, and ask it to discover media and show the results in the picker.

## Test

```bash
uv run pytest examples/apps/media_picker/test_media_picker.py
```
