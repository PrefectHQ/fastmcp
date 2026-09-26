# smart home setup and operation

Control Philips Hue lights and an Amazon Fire TV through FastMCP. Hue uses the
`phue2` 1.0 alpha's local V2 API. Fire TV control reuses the `androidtv` backend
shipped by Home Assistant's Android Debug Bridge integration; raw ADB shell access
is not exposed to agents.

## Design

This example is a local device-control server. An MCP client can discover devices,
issue a command, and read back the result. It does not require Pi, a graphical
picker, or a workflow engine.

- `src/smart_home/hub.py` mounts the independent Hue and Fire TV servers under
  `hue` and `fire_tv` namespaces.
- Each device package owns its connection lifespan, typed results, and tools.
- The optional [media picker](../../apps/media_picker/README.md) presents choices and
  calls the same playback tools over MCP.

Media search and recommendation belong to the calling agent or a discovery
service. Schedules, presence policies, and durable desired state belong to the
calling application or workflow engine. Device tools do not infer those policies.

## Run

From the repository root, enter the example and install its dependencies (Python
3.12 or newer and `uv` are required):

```bash
cd examples/smart_home
uv sync
```

Create `.env` in this directory with your existing bridge credentials:

```dotenv
HUE_BRIDGE_IP=<bridge IP>
HUE_BRIDGE_USERNAME=<bridge application key>
HUE_BRIDGE_CERTIFICATE=/absolute/path/to/trusted-bridge.pem
FIRE_TV_HOST=<Fire TV IP>
FIRE_TV_ADB_SERVER_IP=127.0.0.1
```

HTTPS verification is enabled. The optional certificate file is an explicitly
trusted certificate obtained and verified for your bridge; its identity replaces
hostname matching when connecting by IP. Without it, normal system trust and
hostname verification apply. Credentials remain local and are not saved by the SDK.

`FIRE_TV_HOST` is optional. When omitted, Hue tools continue to work and Fire TV
tools return a configuration error. The example supports either an existing ADB
server via `FIRE_TV_ADB_SERVER_IP` or direct Python ADB authentication via
`FIRE_TV_ADB_KEY`. Enable ADB debugging and approve the host on the TV first.

```bash
uv run smart-home
```

This starts a stdio MCP server; configure your MCP client to run this command with
this directory as its working directory. The combined hub requires Hue settings.
For TV-only use, run the Fire TV server independently:

```bash
uv run fastmcp run src/smart_home/fire_tv/server.py:fire_tv_mcp --transport http --host 127.0.0.1 --port 8764
```

Standalone tools are named `read_status` and `play_media`; through the hub they
are `fire_tv_read_status` and `fire_tv_play_media`. Use the standalone HTTP endpoint
`http://127.0.0.1:8764/mcp` with the media picker's `play_media` configuration.

The server owns pooled asynchronous device connections in its lifespan. Tools
receive those existing connections through dependency injection. Settings load at
startup, so importing the example does not require live credentials.

## Agent workflow

### TV and ad hoc media selection

Use `fire_tv_read_status` before and after `fire_tv_press_home`,
`fire_tv_launch_app`, or `fire_tv_play_youtube_video`. App launch requires an exact
installed package ID; YouTube playback requires an 11-character video ID. Commands
return acceptance receipts, not proof that navigation completed. The constrained
surface deliberately exposes neither arbitrary URLs nor raw ADB shell commands.

For a request such as "play a live bird camera where it is daylight": find a
current feed, check the camera's location and local time, verify that the video
is live and playable, then call `fire_tv_play_youtube_video` with its video ID.
Use the broadcaster's current live page: old livestream links can return
"recording is not available." This workflow does not require editing a catalog
or adding a new tool for every video.

Status verifies the foreground app and broad playback state, not the video's
identity, daylight, or audible content. Inspect the actual picture or obtain
device media metadata when those details matter. A command receipt alone is not
evidence that the requested stream is playing.

### Hue rooms, scenes, and effects

Start with `hue_read_rooms` and `hue_read_lights`. Rooms include member light UUIDs;
lights include state, device connectivity and supported effects. Names must match
exactly and be unique. V2 UUIDs replace the old numeric light and group IDs.

To turn on candle flicker, check each room member's `supported_effects` and call
`hue_set_light` for each supported bulb:

```json
{
  "target": "<light UUID>",
  "state": {"on": true, "effect": "candle", "effect_speed": 0.5}
}
```

This preserves brightness. Read `state.effect` and `state.effect_parameters` afterward to verify the active
effect; use `effect: "no_effect"` to stop it. Effect names and support come from the
bulb, not a fixed list. Speed is between zero and one; color or temperature supplied
with an active effect changes its parameters.

For ordinary room-wide lighting, use `hue_set_room` with brightness percent,
`temperature_kelvin`, and optional `transition_seconds`. Color can instead use CIE
`xy` coordinates. Native effects target individual bulbs. Brightness, color and
effects do not implicitly turn lights on; include `on: true` when desired.

Use `hue_read_scenes` to inspect room associations, palettes and per-light actions.
That distinguishes a scene with warm static colors from one with candle or fire
effects. `hue_activate_scene` resolves names within the chosen room and recalls the
saved actions. Its optional `dynamic_palette` action requests palette cycling where
supported by Hue.

A write acknowledgement does not prove the resulting state. Read lights after a
transition. Hue may partially apply a command before reporting an error; failures
are exposed as MCP tool errors. This example does not implement scheduling,
custom animation loops, or entertainment streaming.

## Test with an agent

```bash
uv run pytest
uv run scripts/pi_harness.py --json
```

The tests exercise actual MCP calls with a simulated bridge. The Pi harness
requires Pi and `pi-mcp-adapter`, exposes only this MCP, disables built-in tools,
and defaults to read-only discovery. Pass `--env-file /path/to/existing.env` if
credentials live elsewhere; `HUE_BRIDGE_CERTIFICATE` can also be exported in the
launching environment. A quoted prompt may request real changes to lights.
`--json` records tool calls and results for verification.

## Discovery and result metadata

`hue_read_lights(room="living room")` and `hue_read_scenes(room="living room")`
limit discovery to a room, using its exact unique name or UUID. Light results put
observed state, supported effects and capabilities first. `details=true` includes
the complete Hue light resource when needed. Unknown observations remain null;
color temperature is reported only when Hue marks it valid.

`hue_set_room` exposes ordinary lighting controls only. Apply native effects with
`hue_set_light` to each supported bulb. All writes return an accepted receipt with
`state_verified=false`; read the affected room after a transition to verify state.
Repeating an effect or scene command may restart its animation or transition, so
write tools do not promise idempotence. Tools interact with the external bridge.

## Connection troubleshooting

With the ADB-server transport, first check `adb devices` and connect with
`adb connect <Fire TV IP>:5555`. If the TV is reachable but ADB reports a missing
device, reconnect it. If TCP port 5555 is reachable but ADB still reports "No route
to host," restarting the local ADB daemon can recover it: `adb kill-server`,
`adb start-server`, then connect again. Restarting the daemon disconnects other
ADB devices too; check its device list first.

The current example does not supervise the ADB daemon or reconnect indefinitely.
A configured but unavailable TV can fail server startup. Use the separate device
servers when one device's availability must not block the other.
