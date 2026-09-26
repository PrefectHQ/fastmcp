# Media Picker MCP App

An interactive media picker built with `FastMCPApp` and Prefab. The model finds media
with its own search; the server verifies what it found and plays the user's choice:

1. The model passes YouTube links to `show_media_picker`.
2. The server checks each link with YouTube's oEmbed endpoint, which supplies the real
   title and channel, and renders the playable ones as cards. Links it can't verify or
   play are counted, not shown.
3. `Play` and `Save` call app-only tools without another model turn.
4. `More like this` sends a focused follow-up request back to the conversation.

Nothing is hardcoded: whatever the model finds is what appears. `Save` stores items in
memory until the server restarts. Demo playback returns a receipt without controlling
hardware. To control a real device, point it at an MCP server with a playback tool
that accepts `source`, `source_id`, `url`, and `title`:

```bash
export MEDIA_PICKER_ACTUATOR_URL=http://127.0.0.1:8764/mcp
export MEDIA_PICKER_ACTUATOR_TOOL=play_media
export MEDIA_PICKER_ACTUATOR_SOURCES=youtube
```

The [smart-home example](../../smart_home/README.md) documents how to start the
standalone Fire TV endpoint used above. If you expose its combined hub instead,
set `MEDIA_PICKER_ACTUATOR_TOOL=fire_tv_play_media`. When an actuator is configured,
`MEDIA_PICKER_ACTUATOR_SOURCES` is required: the picker hides unsupported results and
direct calls fail before dispatch.

## Home view and lights

`show_home` combines the TV picks with a Lights tab: every Hue room with its live
color, an on/off switch, brightness presets, and its saved scenes. It reads and
controls lights through the smart-home example's Hue server over MCP:

```bash
export MEDIA_PICKER_LIGHTS_URL=http://127.0.0.1:8766/mcp
```

The light buttons call app-only tools, so the model never changes lights on its own.
On macOS, start the Hue server from a process with Local Network permission: one
launched without it gets `No route to host` from the bridge.

## Sign-in

A picker that controls a TV shouldn't be open to anyone who finds its URL. Setting
`MEDIA_PICKER_BASE_URL` turns on [AT Protocol sign-in](https://gofastmcp.com/integrations/atproto):
people sign in with their handle, and only the listed DIDs get in.

```bash
export MEDIA_PICKER_BASE_URL=https://your-tunnel.example
export MEDIA_PICKER_ALLOWED_DIDS=did:plc:your-did
export MEDIA_PICKER_JWT_SIGNING_KEY=a-long-random-secret
```

Keep the base URL and signing key stable: the base URL is the sign-in's client ID, and
a new signing key ends every session.

With one allowed DID, signing in goes straight to that account's PDS. The consent
screen shows once per client in each browser, then is remembered.

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

To use it from Claude or ChatGPT, expose the server through a public HTTPS tunnel,
connect the resulting `/mcp` URL, and ask for something to watch.

## Test

```bash
uv run pytest examples/apps/media_picker/test_media_picker.py
```
