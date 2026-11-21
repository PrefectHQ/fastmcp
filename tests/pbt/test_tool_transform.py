"""A transformed tool behaves like its parent with the arguments mapped.

A parent tool with drawn parameters (types, defaults) gets drawn argument
transforms (rename, hide, new default, required, description), optionally a
second layer of transforms on top, and is called through an in-memory Client.
The model follows docs/servers/transforms/tool-transformation.mdx:

- construction is refused exactly when the docs or `ArgTransform` say so: a
  hidden argument with no default anywhere, two arguments mapped to one name,
  `default_factory` without `hide`, `required=True` with a default, `hide`
  with `required`; a deferred config that cannot apply hides only its own
  tool, and every other tool still lists and runs
- the listed schema has the visible arguments under their new names, required
  exactly when the parent requires them and no transform gave a default, or a
  transform made them required
- a call reaches the parent with every value mapped back to its original
  name; an omitted argument gets the transform's default, else the parent's;
  a hidden argument always gets the transform's default (or factory), else the
  parent's
- a call that omits a required argument, or names one the tool does not list,
  fails with an error the client receives
"""

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest
from hypothesis import event, given, note
from hypothesis import strategies as st

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import (
    ArgTransform,
    ArgTransformConfig,
    ToolTransformConfig,
)

PARAMS = ["a", "b", "c", "d"]
NEW_NAMES = ["x", "y", "a", "b"]
TYPES: dict[str, tuple[str, st.SearchStrategy[Any]]] = {
    "int": ("int", st.integers(-5, 5)),
    "str": ("str", st.sampled_from(["", "s", "two words"])),
    "bool": ("bool", st.booleans()),
    "list": ("list[int]", st.lists(st.integers(0, 3), max_size=2)),
    "optional": ("str | None", st.sampled_from([None, "o"])),
}
_counter = itertools.count()


@dataclass
class Param:
    name: str
    kind: str
    default: Any = None
    has_default: bool = False


@dataclass
class Visible:
    """One argument a client of a layer sees."""

    origin: str  # the parent tool's parameter name
    default: Any = None
    has_default: bool = False


@dataclass
class Layer:
    """What one layer of the tool accepts and where each value goes."""

    visible: dict[str, Visible] = field(default_factory=dict)
    # origin -> value every call passes, whatever the client sends
    fixed: dict[str, Any] = field(default_factory=dict)


def make_parent(params: list[Param]) -> Tool:
    """A tool whose function returns the arguments it received."""
    sig = ", ".join(
        f"{p.name}: {TYPES[p.kind][0]}" + (f" = {p.default!r}" if p.has_default else "")
        for p in sorted(params, key=lambda p: p.has_default)
    )
    body = "{" + ", ".join(f"{p.name!r}: {p.name}" for p in params) + "}"
    name = f"parent_{next(_counter)}"
    namespace: dict[str, Any] = {}
    exec(f"def {name}({sig}) -> dict:\n    return {body}", namespace)
    return Tool.from_function(namespace[name], name="parent")


@st.composite
def parents(draw: st.DrawFn) -> list[Param]:
    names = draw(st.lists(st.sampled_from(PARAMS), min_size=1, max_size=4, unique=True))
    out = []
    for name in names:
        kind = draw(st.sampled_from(sorted(TYPES)))
        if draw(st.booleans()):
            out.append(Param(name, kind, draw(TYPES[kind][1]), True))
        else:
            out.append(Param(name, kind))
    return out


@st.composite
def arg_transforms(draw: st.DrawFn, names: list[str], kinds: dict[str, str]):
    """Raw ArgTransform options per argument, including invalid combinations."""
    out: dict[str, dict[str, Any]] = {}
    chosen = draw(st.sets(st.sampled_from(names))) if names else set()
    for name in sorted(chosen):
        opts: dict[str, Any] = {}
        if draw(st.booleans()):
            opts["name"] = draw(st.sampled_from(NEW_NAMES))
        if draw(st.integers(0, 3)) == 0:
            opts["hide"] = True
        how = draw(st.sampled_from(["none", "default", "factory", "required"]))
        if how == "default":
            opts["default"] = draw(TYPES[kinds[name]][1])
        elif how == "factory":
            value = draw(TYPES[kinds[name]][1])
            opts["default_factory"] = lambda value=value: value
        elif how == "required":
            opts["required"] = True
        if draw(st.booleans()):
            opts["description"] = "d"
        out[name] = opts
    return out


def arg_transform_refusal(opts: dict[str, Any]) -> str | None:
    """Why `ArgTransform(**opts)` is refused, per its documented rules."""
    has_default = "default" in opts or "default_factory" in opts
    if "default_factory" in opts and not opts.get("hide"):
        return "default_factory without hide"
    if opts.get("required") and has_default:
        return "required with a default"
    if opts.get("hide") and opts.get("required"):
        return "hide with required"
    return None


def apply_layer(
    below: Layer, transforms: dict[str, dict[str, Any]]
) -> tuple[Layer | None, str | None]:
    """The layer a client sees after `transforms`, or why from_tool refuses."""
    above = Layer(fixed=dict(below.fixed))
    for name, arg in below.visible.items():
        opts = transforms.get(name, {})
        has_default = arg.has_default
        default = arg.default
        if "default" in opts:
            has_default, default = True, opts["default"]
        elif "default_factory" in opts:
            has_default, default = True, opts["default_factory"]()
        if opts.get("required"):
            has_default, default = False, None
        if opts.get("hide"):
            if not has_default:
                return None, "hidden argument with no default"
            above.fixed[arg.origin] = default
            continue
        new = opts.get("name", name)
        if new in above.visible:
            return None, "two arguments mapped to one name"
        above.visible[new] = Visible(arg.origin, default, has_default)
    return above, None


def schema_view(tool) -> dict[str, tuple[bool, Any]]:
    """name -> (required, default) as listed."""
    props = tool.input_schema.get("properties", {})
    required = set(tool.input_schema.get("required", []))
    return {n: (n in required, s.get("default")) for n, s in props.items()}


LOOP = asyncio.new_event_loop()


@pytest.fixture(autouse=True)
def _quiet():
    logger = logging.getLogger("fastmcp")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    yield
    logger.setLevel(level)


@pytest.fixture(autouse=True, scope="module")
def _close_loop():
    yield
    LOOP.close()


async def _refused_deferred(parent: Tool, config: ToolTransformConfig) -> None:
    """A config that cannot apply hides its own tool and nothing else."""
    server = FastMCP("s")
    server.add_tool(parent)
    server.add_tool(Tool.from_function(lambda: "ok", name="other"))
    server.add_transform(ToolTransform({"parent": config}))
    async with Client(server) as client:
        listed = [t.name for t in await client.list_tools()]
        assert listed == ["other"], "a transform that cannot apply was listed"
        result = await client.call_tool("other", {})
        assert result.content[0].text == "ok", "an unrelated tool failed"
        for name in ("t0", "parent"):
            with pytest.raises(ToolError):
                await client.call_tool(name, {})


@given(data=st.data())
def test_transformed_tool_matches_model(data):
    params = data.draw(parents(), label="parent")
    kinds = {p.name: p.kind for p in params}
    parent = make_parent(params)
    layer = Layer(
        visible={p.name: Visible(p.name, p.default, p.has_default) for p in params}
    )
    tool: Tool = parent
    # The deferred path (a server transform from config) takes one layer of
    # JSON-representable options; Tool.from_tool takes anything, stacked.
    deferred = data.draw(st.booleans(), label="deferred")
    config: ToolTransformConfig | None = None
    depth = 1 if deferred else data.draw(st.integers(1, 2), label="layers")
    for level in range(depth):
        names = sorted(layer.visible)
        layer_kinds = {n: kinds[layer.visible[n].origin] for n in names}
        raw = data.draw(arg_transforms(names, layer_kinds), label=f"layer {level}")
        refusal = next(
            (r for opts in raw.values() if (r := arg_transform_refusal(opts))), None
        )
        try:
            built = {n: ArgTransform(**opts) for n, opts in raw.items()}
        except ValueError:
            assert refusal, f"ArgTransform refused valid options: {raw}"
            event("refused: ArgTransform")
            return
        assert not refusal, f"ArgTransform accepted {refusal}: {raw}"
        new_layer, refusal = apply_layer(layer, raw)
        if deferred and all(
            "default_factory" not in o and not isinstance(o.get("default"), list)
            for o in raw.values()
        ):
            config = ToolTransformConfig(
                name="t0",
                arguments={n: ArgTransformConfig(**o) for n, o in raw.items()},
            )
            if refusal:
                event(f"refused deferred: {refusal}")
                LOOP.run_until_complete(_refused_deferred(parent, config))
                return
            assert new_layer is not None
            layer = new_layer
            break
        try:
            tool = Tool.from_tool(tool, name=f"t{level}", transform_args=built)
        except ValueError:
            assert refusal, f"from_tool refused a valid transform: {raw}"
            event(f"refused: {refusal}")
            return
        assert new_layer is not None, f"from_tool accepted {refusal}: {raw}"
        layer = new_layer
    event("built: deferred" if config else f"built: {depth} layer(s)")
    tool_name = "t0" if config else tool.name

    want_schema = {n: (not v.has_default, None) for n, v in layer.visible.items()}

    # A call: each visible argument sent or (when optional) omitted, plus,
    # sometimes, an argument the tool does not list.
    sent: dict[str, Any] = {}
    parent_defaults = {p.name for p in params if p.has_default}
    for name, arg in sorted(layer.visible.items()):
        # Omitting an argument only a transform made required is the case the
        # parent's own validation cannot catch, so draw it far more often.
        odds = 3 if arg.origin in parent_defaults else 19
        if not arg.has_default and data.draw(st.integers(0, odds), label="omit") == 0:
            continue
        if arg.has_default and data.draw(st.booleans(), label=f"omit {name}"):
            continue
        sent[name] = data.draw(TYPES[kinds[arg.origin]][1], label=f"send {name}")
    stray = None
    if data.draw(st.integers(0, 3), label="stray?") == 0:
        stray = data.draw(st.sampled_from([*PARAMS, *NEW_NAMES]), label="stray")
    if stray is not None and stray not in layer.visible:
        sent[stray] = 1
    else:
        stray = None

    missing = [
        n for n, v in layer.visible.items() if not v.has_default and n not in sent
    ]
    if missing or stray:
        want: dict[str, Any] | None = None
    else:
        want = dict(layer.fixed)
        for name, arg in layer.visible.items():
            want[arg.origin] = sent[name] if name in sent else arg.default
    note(f"sent={sent} want={want}")

    async def run():
        server = FastMCP("s")
        server.add_tool(tool)
        if config is not None:
            server.add_transform(ToolTransform({"parent": config}))
        async with Client(server) as client:
            (listed,) = await client.list_tools()
            got_schema = schema_view(listed)
            assert {n: r for n, (r, _) in got_schema.items()} == {
                n: r for n, (r, _) in want_schema.items()
            }, "the listed arguments or their required flags differ from the model"
            try:
                result = await client.call_tool(tool_name, sent)
            except ToolError as e:
                return None, str(e)
            return result.structured_content, None

    got, error = LOOP.run_until_complete(run())
    if want is None:
        event("call: expected error")
        assert got is None, f"an invalid call ran: sent {sent}, got {got}"
    else:
        event("call: expected ok")
        assert error is None, f"a valid call failed: {error}"
        assert got == want, "the parent received different arguments than the model"
