"""A form a server elicits is one an MCP client can show, and what the user
fills in comes back to the server as the value they chose.

A drawn response type (a scalar, `Literal`, `Enum`, a multi-select list, the
dict and list shorthands, or a `BaseModel` / dataclass of such fields, each
required, defaulted or `T | None`) goes through `ctx.elicit()` on a FastMCP
server. The model follows docs/servers/elicitation.mdx and the MCP 2025-11-25
`PrimitiveSchemaDefinition`:

- the requested schema is a flat object whose every property is one of the
  spec's primitive shapes (string, number/integer, boolean, single-select or
  multi-select enum of strings); a property may carry only the keywords its
  shape defines, besides title and description, because a client that knows
  only the spec drops the rest
- a user who fills in a value for every required field (and any optional ones)
  answers with that value's JSON; the server's `result.data` is the value
  they chose, an omitted defaulted field is its default, an omitted
  `T | None` field is `None`
"""

import asyncio
import enum
import json
import logging
from dataclasses import dataclass, make_dataclass
from dataclasses import field as dc_field
from typing import Annotated, Any, Literal

import pytest
from hypothesis import event, given, note
from hypothesis import strategies as st
from pydantic import Field, TypeAdapter, create_model

from fastmcp import Client, Context, FastMCP
from fastmcp.server.elicitation import parse_elicit_response_type

from .known import still_open

LOOP = asyncio.new_event_loop()
WORDS = ["a", "b", "low", "high", "x y", "ü"]
UNSET = object()

# ---------------------------------------------------------------------------
# the MCP 2025-11-25 PrimitiveSchemaDefinition, as keyword sets
# ---------------------------------------------------------------------------

# Keywords that change what a client lets the user enter. A property may use
# only those its shape defines; title and description are free.
MEANINGFUL = {
    "type", "enum", "enumNames", "oneOf", "anyOf", "const", "items", "minimum",
    "maximum", "minLength", "maxLength", "format", "pattern", "minItems",
    "maxItems", "default", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "$ref", "allOf", "not",
}  # fmt: skip


def _strs(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(i, str) for i in v)


def _titled(v: Any) -> bool:
    return isinstance(v, list) and all(
        isinstance(o, dict)
        and isinstance(o.get("const"), str)
        and isinstance(o.get("title"), str)
        for o in v
    )


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


# shape name -> (keyword -> check of its value)
SHAPES: dict[str, dict[str, Any]] = {
    "boolean": {
        "type": lambda v: v == "boolean",
        "default": lambda v: isinstance(v, bool),
    },
    "string": {
        "type": lambda v: v == "string",
        "minLength": _num,
        "maxLength": _num,
        "format": lambda v: v in {"email", "uri", "date", "date-time"},
        "default": lambda v: isinstance(v, str),
    },
    "number": {
        "type": lambda v: v in {"number", "integer"},
        "minimum": _num,
        "maximum": _num,
        "default": _num,
    },
    "single-select": {
        "type": lambda v: v == "string",
        "enum": _strs,
        "enumNames": _strs,
        "default": lambda v: isinstance(v, str),
    },
    "titled single-select": {
        "type": lambda v: v == "string",
        "oneOf": _titled,
        "default": lambda v: isinstance(v, str),
    },
    "multi-select": {
        "type": lambda v: v == "array",
        "items": lambda v: isinstance(v, dict)
        and v.get("type") == "string"
        and _strs(v.get("enum"))
        and not (set(v) & MEANINGFUL) - {"type", "enum"},
        "minItems": _num,
        "maxItems": _num,
        "default": _strs,
    },
    "titled multi-select": {
        "type": lambda v: v == "array",
        "items": lambda v: isinstance(v, dict)
        and _titled(v.get("anyOf"))
        and not (set(v) & MEANINGFUL) - {"anyOf"},
        "minItems": _num,
        "maxItems": _num,
        "default": _strs,
    },
}
REQUIRED_KEYS = {
    "single-select": {"enum"},
    "titled single-select": {"oneOf"},
    "multi-select": {"items"},
    "titled multi-select": {"items"},
}


def spec_shape(prop: dict[str, Any]) -> str | None:
    """The spec shape a property matches, or None."""
    used = set(prop) & MEANINGFUL
    for name, keys in SHAPES.items():
        if "type" not in prop or not used <= set(keys):
            continue
        if not REQUIRED_KEYS.get(name, set()) <= used:
            continue
        if all(keys[k](prop[k]) for k in used):
            return name
    return None


# ---------------------------------------------------------------------------
# response types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """One field: its kind, choices, and whether it is required."""

    kind: str
    choices: tuple = ()
    bound: int | None = None
    mode: str = "required"  # required | default | optional
    default: Any = UNSET


KINDS = [
    "str", "int", "float", "bool", "literal str", "literal int", "str enum",
    "int enum", "multi literal", "multi enum",
]  # fmt: skip
_enums: dict[tuple, type[enum.Enum]] = {}


def enum_of(choices: tuple) -> type[enum.Enum]:
    if choices not in _enums:
        _enums[choices] = enum.Enum(  # type: ignore[misc]
            f"E{len(_enums)}", {f"M{i}": c for i, c in enumerate(choices)}
        )
    return _enums[choices]


def py_type(s: Spec) -> Any:
    if s.kind == "str":
        return str if s.bound is None else Annotated[str, Field(min_length=s.bound)]
    if s.kind == "int":
        return int if s.bound is None else Annotated[int, Field(ge=s.bound)]
    if s.kind == "float":
        return float
    if s.kind == "bool":
        return bool
    if s.kind in {"literal str", "literal int"}:
        return Literal[s.choices]  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    if s.kind in {"str enum", "int enum"}:
        return enum_of(s.choices)
    if s.kind == "multi literal":
        return list[Literal[s.choices]]  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    return list[enum_of(s.choices)]  # ty:ignore[invalid-type-form]


def values(s: Spec) -> st.SearchStrategy:
    if s.kind == "str":
        return st.text(st.sampled_from("ab ü,"), min_size=s.bound or 0, max_size=4)
    if s.kind == "int":
        return st.integers(s.bound if s.bound is not None else -5, 50)
    if s.kind == "float":
        return st.floats(-10, 10, allow_nan=False) | st.integers(-3, 3).map(float)
    if s.kind == "bool":
        return st.booleans()
    if s.kind in {"literal str", "literal int"}:
        return st.sampled_from(s.choices)
    if s.kind in {"str enum", "int enum"}:
        return st.sampled_from(list(enum_of(s.choices)))
    item = (
        st.sampled_from(s.choices)
        if s.kind == "multi literal"
        else st.sampled_from(list(enum_of(s.choices)))
    )
    return st.lists(item, max_size=3, unique=True)


@st.composite
def specs(draw) -> Spec:
    kind = draw(st.sampled_from(KINDS))
    choices: tuple = ()
    bound = None
    if kind in {"literal int", "int enum"}:
        choices = tuple(
            draw(st.lists(st.integers(0, 3), min_size=1, max_size=3, unique=True))
        )
    elif kind in {"literal str", "str enum", "multi literal", "multi enum"}:
        choices = tuple(
            draw(st.lists(st.sampled_from(WORDS), min_size=1, max_size=3, unique=True))
        )
    elif kind in {"str", "int"} and draw(st.booleans()):
        bound = draw(st.integers(0, 2))
    s = Spec(kind, choices, bound)
    mode = draw(st.sampled_from(["required", "default", "optional"]))
    if mode == "default":
        return Spec(kind, choices, bound, mode, draw(values(s)))
    return Spec(kind, choices, bound, mode)


@dataclass
class Form:
    """A response type, the fields it asks for, and how its data reads back."""

    response_type: Any
    fields: dict[str, Spec]
    container: str  # scalar | dict shorthand | ... | model | dataclass

    def expected(self, answers: dict[str, Any]) -> Any:
        filled = {}
        for name, s in self.fields.items():
            if name in answers:
                filled[name] = answers[name]
            elif s.mode == "default":
                filled[name] = s.default
            else:
                filled[name] = None
        if self.container in {"model", "dataclass"}:
            return self.response_type(**filled)
        return filled["value"]


def _field_type(s: Spec) -> Any:
    t = py_type(s)
    return t | None if s.mode == "optional" else t


@st.composite
def forms(draw) -> Form:
    container = draw(
        st.sampled_from(
            [
                "scalar",
                "dict shorthand",
                "list shorthand",
                "multi shorthand",
                "model",
                "dataclass",
            ]
        )
    )
    if container == "dict shorthand":
        choices = tuple(
            draw(st.lists(st.sampled_from(WORDS), min_size=1, max_size=3, unique=True))
        )
        return Form(
            {c: {"title": c.upper()} for c in choices},
            {"value": Spec("literal str", choices)},
            container,
        )
    if container == "list shorthand":
        choices = tuple(
            draw(st.lists(st.sampled_from(WORDS), min_size=1, max_size=3, unique=True))
        )
        return Form(list(choices), {"value": Spec("literal str", choices)}, container)
    if container == "multi shorthand":
        choices = tuple(
            draw(st.lists(st.sampled_from(WORDS), min_size=1, max_size=3, unique=True))
        )
        titled = draw(st.booleans())
        rt = [{c: {"title": c.upper()} for c in choices}] if titled else [list(choices)]
        return Form(rt, {"value": Spec("multi literal", choices)}, container)
    if container == "scalar":
        s = draw(specs().filter(lambda s: s.mode == "required" and s.bound is None))
        return Form(py_type(s), {"value": s}, container)
    names = [f"f{i}" for i in range(draw(st.integers(1, 3)))]
    fields = {n: draw(specs()) for n in names}
    if container == "model":
        defs = {}
        for n, s in fields.items():
            t = _field_type(s)
            if s.mode == "required":
                defs[n] = (t, ...)
            elif s.mode == "optional":
                defs[n] = (t, None)
            else:
                defs[n] = (t, s.default)
        return Form(create_model("Answer", **defs), fields, container)  # type: ignore[call-overload]  # ty:ignore[no-matching-overload]
    ordered = sorted(fields.items(), key=lambda kv: kv[1].mode != "required")
    dc_fields = []
    for n, s in ordered:
        t = _field_type(s)
        if s.mode == "required":
            dc_fields.append((n, t))
        elif s.mode == "optional":
            dc_fields.append((n, t, dc_field(default=None)))
        else:
            d = s.default
            dc_fields.append((n, t, dc_field(default_factory=lambda d=d: d)))
    return Form(make_dataclass("Answer", dc_fields), dict(ordered), container)


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


def to_json(value: Any) -> Any:
    return json.loads(json.dumps(TypeAdapter(Any).dump_python(value, mode="json")))


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------


def _f14_probe() -> bool:
    """F14: a choice of one option is sent as `const` (no spec shape), and a
    multi-select of one option is refused when the server builds the form."""
    schema = parse_elicit_response_type(["a"]).schema
    return spec_shape(schema["properties"]["value"]) is None


def _f15_probe() -> bool:
    """F15: an integer `Literal` or `Enum` is sent as an integer enum, which no
    spec shape has (enums are strings), so a spec client drops the choices."""
    schema = parse_elicit_response_type(Literal[1, 2]).schema
    return spec_shape(schema["properties"]["value"]) is None


F14_OPEN = still_open("F14", _f14_probe)
F15_OPEN = still_open("F15", _f15_probe)
INT_CHOICES = {"literal int", "int enum"}


def known(s: Spec) -> str | None:
    """The open finding one field runs into, if any."""
    if F14_OPEN and len(s.choices) == 1:
        return "F14"
    if F15_OPEN and s.kind in INT_CHOICES:
        return "F15"
    return None


def refused_for_known(form: Form, error: TypeError) -> str | None:
    """The open finding behind a refused form: the field the error names."""
    for name, s in form.fields.items():
        if f"field '{name}'" in str(error) and known(s) == "F14":
            return "F14"
    return None


@given(form=forms())
def test_every_requested_schema_is_a_spec_form(form):
    try:
        config = parse_elicit_response_type(form.response_type)
    except TypeError as e:
        if refused_for_known(form, e):
            event("known: F14")
            return
        raise
    schema = config.schema
    note(json.dumps(schema))
    assert schema.get("type") == "object"
    props = schema.get("properties", {})
    assert set(props) == set(form.fields), "the form asks for other fields"
    required = set(schema.get("required", []))
    assert required <= set(props)
    for name, s in form.fields.items():
        want_required = s.mode == "required"
        assert (name in required) == want_required, f"{name}: wrong required flag"
        shape = spec_shape(props[name])
        if shape is None and (finding := known(s)):
            event(f"known: {finding}")
            continue
        assert shape is not None, f"{name}: {props[name]} is no spec shape"
        event(f"{s.kind} -> {shape}")


async def _elicit(form: Form, answers: dict[str, Any]) -> Any:
    got: list[Any] = []
    server = FastMCP("elicit")

    @server.tool
    async def ask(ctx: Context) -> str:
        result = await ctx.elicit("fill this in", response_type=form.response_type)
        assert result.action == "accept", result
        got.append(result.data)
        return "ok"

    async def handler(message, response_type, params, context):
        return dict(answers)

    async with Client(server, mode="legacy", elicitation_handler=handler) as client:
        await client.call_tool("ask", {})
    return got[0]


@given(data=st.data(), form=forms())
def test_the_server_gets_what_the_user_chose(data, form):
    chosen: dict[str, Any] = {}
    for name, s in form.fields.items():
        if s.mode == "required" or data.draw(st.booleans(), label=f"fill {name}"):
            chosen[name] = data.draw(values(s), label=name)
    answers = {k: to_json(v) for k, v in chosen.items()}
    note(f"answers {answers}")
    want = form.expected(chosen)
    try:
        parse_elicit_response_type(form.response_type)
    except TypeError as e:
        if refused_for_known(form, e):
            event("known: F14")
            return
        raise
    got = LOOP.run_until_complete(_elicit(form, answers))
    note(f"got {got!r}, want {want!r}")
    assert got == want
    event(f"{form.container}: {len(answers)}/{len(form.fields)} filled")
