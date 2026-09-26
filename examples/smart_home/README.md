# smart home MCP

Control Philips Hue lights and an Amazon Fire TV through one FastMCP server.
The example demonstrates namespaced device tools, pooled connections, typed
receipts, and reading device state after a command. It uses `phue2` for Hue's
local V2 API and Home Assistant's `androidtv` backend for Fire TV.

## run

Requires Python 3.12+, `uv`, and credentials for your Hue bridge. From the
repository root:

```bash
cd examples/smart_home
uv sync
```

Create `.env` in this directory:

```dotenv
HUE_BRIDGE_IP=<bridge IP>
HUE_BRIDGE_USERNAME=<bridge application key>
HUE_BRIDGE_CERTIFICATE=/absolute/path/to/trusted-bridge.pem
```

For Fire TV, also set `FIRE_TV_HOST` and `FIRE_TV_ADB_SERVER_IP=127.0.0.1`.
Enable ADB debugging, approve this computer on the TV, and connect the ADB server
to the device first. The [setup and operation guide](docs/usage.md) covers
certificate trust, direct ADB authentication, TV-only startup, and troubleshooting.

```bash
uv run smart-home
```

This is a stdio MCP server. Configure your client to run that command with this
directory as its working directory. Discover `hue_` and `fire_tv_` tools in the
client, read the target device, issue a command, and read it again. Write receipts
confirm command acceptance; they do not prove the resulting device state.

## design

`src/smart_home/hub.py` mounts independent `lights/` and `fire_tv/` servers. Each
owns its transport and connection lifespan. The hub requires Hue configuration;
the Fire TV server can also run independently.

An agent can find a current daylight wildlife feed and pass its video ID to
`fire_tv_play_youtube_video` without changing this example. The optional
[media picker](../apps/media_picker/README.md) adds a graphical selection surface
over the same playback tools. Its catalog is sample data, not a search service.

Schedules, presence rules, and durable desired state belong to the calling
application or workflow engine. This example provides the device operations that
those policies compose. It does not require Pi or a particular UI.

## develop

From this directory:

```bash
uv run pytest
uv run scripts/pi_harness.py --json
```

Tests dispatch real MCP calls against simulated devices. The optional Pi harness
requires Pi and `pi-mcp-adapter` and defaults to read-only Hue inspection. Hardware
verification is separate; see the [operation guide](docs/usage.md) for workflows
and the limits of status readback.
