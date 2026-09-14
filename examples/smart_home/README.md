# smart home MCP

Control Philips Hue lights through FastMCP and the `phue2` 1.0 alpha's local Hue
V2 API. An agent can discover real state and capabilities, inspect saved scenes,
and activate native candle or fire effects. The bridge runs those effects;
no agent polling loop is needed.

## Run

Create `.env` in this directory with your existing bridge credentials:

```dotenv
HUE_BRIDGE_IP=<bridge IP>
HUE_BRIDGE_USERNAME=<bridge application key>
HUE_BRIDGE_CERTIFICATE=/absolute/path/to/trusted-bridge.pem
```

HTTPS verification is enabled. The optional certificate file is an explicitly
trusted certificate obtained and verified for your bridge; its identity replaces
hostname matching when connecting by IP. Without it, normal system trust and
hostname verification apply. Credentials remain local and are not saved by the SDK.

```bash
uv run smart-home
```

The server owns one pooled asynchronous bridge connection in its lifespan. Tools
receive that existing connection through dependency injection. Settings load at
startup, so importing the example does not require live credentials.

## Agent workflow

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

Clients should rediscover tools after this update: the former group tools are now
`hue_read_rooms` and `hue_set_room`, and scene activation takes a `room` argument.
