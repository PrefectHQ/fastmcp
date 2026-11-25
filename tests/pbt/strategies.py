"""Hypothesis strategies for MCP-shaped inputs."""

from dataclasses import dataclass, field
from typing import Any

from hypothesis import strategies as st

# Literals between expressions hold only characters an encoded simple value
# never contains unencoded, so a match cannot split a value differently.
SEPARATORS = ["/", ":", "!", "@", "$", "/-/", "://"]
# Leading literals may hold anything, including text `encode_literal` must
# percent-encode, since nothing precedes them.
LEADING = ["res://x/", "data://", "file:///docs/café/", "data://a b/", "x:"]

# A query value or simple value under `quote(safe="")` round-trips any text.
VALUE_PIECES = [
    "a", "b", "9", " ", "é", "%", "/", "?", "#", "&", ",", "=", ";", "+",
    "😀", ":", "!", "@", "$", "'", ".", "..", "-", "_", "~", "%41", "%2F",
]  # fmt: skip


def text_from(pieces: list[str], *, min_size: int = 0) -> st.SearchStrategy[str]:
    return st.lists(st.sampled_from(pieces), min_size=min_size, max_size=5).map("".join)


def any_value(*, min_size: int = 0) -> st.SearchStrategy[str]:
    return st.one_of(
        text_from(VALUE_PIECES, min_size=min_size),
        st.text(min_size=min_size, max_size=8),
    )


@dataclass
class Var:
    name: str
    kind: str  # simple, wildcard, query, continuation, literal_query, exploded, list
    value: Any


@dataclass
class DrawnTemplate:
    text: str
    vars: list[Var] = field(default_factory=list)

    @property
    def params(self) -> dict[str, Any]:
        return {v.name: v.value for v in self.vars}

    @property
    def list_params(self) -> set[str]:
        return {v.name for v in self.vars if v.kind == "list"}


@st.composite
def uri_templates(draw: st.DrawFn, *, names: st.SearchStrategy[str] | None = None):
    """A template in fastmcp's RFC 6570 subset, with values for its variables.

    At most one `{name*}` wildcard, since two are ambiguous by construction.
    A query is either `{?..}`/`{&..}` expressions or a literal `?k={v}` run,
    never both.
    """
    counter = iter(range(100))

    def fresh() -> str:
        i = next(counter)
        if names is None:
            return f"v{i}"
        return draw(names) + str(i)

    t = DrawnTemplate(text=draw(st.sampled_from(LEADING)))
    wildcard_used = False
    for i in range(draw(st.integers(1, 3))):
        if i > 0:
            t.text += draw(st.sampled_from(SEPARATORS))
        name = fresh()
        if not wildcard_used and draw(st.integers(0, 3)) == 0:
            wildcard_used = True
            value = draw(text_from(VALUE_PIECES, min_size=1))
            t.text += f"{{{name}*}}"
            t.vars.append(Var(name, "wildcard", value))
        else:
            t.text += f"{{{name}}}"
            t.vars.append(Var(name, "simple", draw(any_value(min_size=1))))

    query = draw(st.sampled_from(["none", "expr", "literal"]))
    if query == "expr":
        names_in = []
        for _ in range(draw(st.integers(1, 3))):
            name = fresh()
            kind = draw(st.sampled_from(["query", "exploded", "list"]))
            if kind == "exploded":
                names_in.append(f"{name}*")
                value = draw(st.lists(any_value(), min_size=1, max_size=3))
            elif kind == "list":
                names_in.append(name)
                value = draw(st.lists(any_value(), min_size=1, max_size=3))
            else:
                names_in.append(name)
                value = draw(any_value())
            t.vars.append(Var(name, kind, value))
        t.text += "{?" + ",".join(names_in) + "}"
        if draw(st.booleans()):
            name = fresh()
            t.text += f"{{&{name}}}"
            t.vars.append(Var(name, "continuation", draw(any_value())))
    elif query == "literal":
        for j in range(draw(st.integers(1, 2))):
            name = fresh()
            key = draw(st.sampled_from(["q", "k", "page"])) + str(j)
            t.text += ("?" if j == 0 else "&") + f"{key}={{{name}}}"
            t.vars.append(Var(name, "literal_query", draw(any_value())))
    return t
