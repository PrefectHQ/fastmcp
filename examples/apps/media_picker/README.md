# Media Picker MCP App

An interactive, source-agnostic media picker built with `FastMCPApp` and Prefab.
It demonstrates a decoupled flow:

1. `discover_media` returns normalized candidates that the model can reason about.
2. `show_media_picker` renders selected candidate IDs as an in-conversation carousel.
3. `Play` and `Save` call app-only tools without another model turn.
4. `More like this` sends a focused follow-up request back to the conversation.

The demo uses a static catalog and returns playback receipts by default. To control a
real device, point it at an MCP server with a playback tool that accepts `source`,
`source_id`, `url`, and `title`:

```dotenv
MEDIA_PICKER_ACTUATOR_URL=http://127.0.0.1:8764/mcp
MEDIA_PICKER_ACTUATOR_TOOL=fire_tv_play_media
```

The smart-home example implements this contract for YouTube on Fire TV. Other sources
fail closed until that actuator supports them.

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
