"""Properties that sanitize to the same Python parameter name (``a-b`` /
``a_b``, ``user.name`` / ``user_name``, and the three-way ``a-b`` / ``a_b`` /
``a_b_2``) must be disambiguated like colliding CLI flags are, not abort the
whole script.

JSON Schema allows such property pairs and OpenAPI routes commonly produce
them; they cannot come from Python signatures, so the regressions use
synthetic ``mcp_types.Tool`` schemas. See #6629.

The executable cases run the generated command through cyclopts with the
flags ``_resolve_tool_param_flags`` assigns, and assert the ``_call_tool``
invocation carries the original schema names — the triage-bot scenario where
a naive suffix used to produce a flag spelling SKILL.md never mentioned.
"""

from typing import Any

import mcp_types

from fastmcp.cli.generate import _resolve_tool_param_flags, generate_cli_script


def _tool(*props: tuple[str, dict[str, Any]]) -> mcp_types.Tool:
    return mcp_types.Tool(
        name="do",
        input_schema={
            "type": "object",
            "properties": dict(props),
        },
    )


def _generate(tools: list[mcp_types.Tool]) -> tuple[str, dict[str, Any]]:
    script = generate_cli_script(
        server_name="desktop",
        server_spec="desktop",
        transport_code=repr("http://unused/mcp"),
        extra_imports=set(),
        tools=tools,
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    return script, namespace


def _effective_flags(props: dict[str, Any]) -> dict[str, str]:
    return {
        prop: explicit or default
        for prop, (default, explicit) in _resolve_tool_param_flags(props).items()
    }


def test_hyphen_underscore_pair_is_disambiguated() -> None:
    props = {"a-b": {"type": "string"}, "a_b": {"type": "string"}}
    script, _ = _generate([_tool(*props.items())])

    assert "a_b_2" in script
    arguments = script.split("_arguments = {", 1)[1].split("}", 1)[0]
    assert "'a-b':" in arguments
    assert "'a_b':" in arguments


def test_dotted_pair_is_disambiguated() -> None:
    props = {"user.name": {"type": "string"}, "user_name": {"type": "string"}}
    script, _ = _generate([_tool(*props.items())])

    assert "user_name_2" in script
    assert "'user.name':" in script


def test_three_way_collision_keeps_every_property_usable() -> None:
    props = {
        "a-b": {"type": "string"},
        "a_b": {"type": "string"},
        "a_b_2": {"type": "string"},
    }
    script, namespace = _generate([_tool(*props.items())])

    # The real a_b_2 property keeps its natural name; only the colliders get
    # suffixed, and no suffix shadows another property's base name.
    import ast

    tree = ast.parse(script)
    params: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name.startswith("tool_"):
                params = [a.arg for a in node.args.args + node.args.kwonlyargs]
    assert sorted(params) == ["a_b", "a_b_2", "a_b_3"]


async def test_three_way_collision_round_trips_through_cyclopts() -> None:
    props = {
        "a-b": {"type": "string"},
        "a_b": {"type": "string"},
        "a_b_2": {"type": "string"},
    }
    script, namespace = _generate([_tool(*props.items())])

    flags = _effective_flags(props)
    captured: list[dict[str, Any]] = []

    async def capture(name: str, arguments: dict[str, Any]) -> str:
        captured.append(arguments)
        return "ok"

    namespace["_call_tool"] = capture
    argv = ["call-tool", "do"]
    for prop in ("a-b", "a_b", "a_b_2"):
        argv.extend([flags[prop], f"v-{prop}"])

    await namespace["app"].run_async(argv, result_action="return_value")

    assert len(captured) == 1
    assert captured[0] == {
        "a-b": "v-a-b",
        "a_b": "v-a_b",
        "a_b_2": "v-a_b_2",
    }
