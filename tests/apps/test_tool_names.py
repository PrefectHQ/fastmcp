"""App tool names must not be mistaken for generated identity addresses."""

import pytest

from fastmcp import FastMCPApp
from fastmcp.tools.base import Tool


@pytest.mark.parametrize("kind", ["tool", "ui"])
@pytest.mark.parametrize("naming", ["function", "positional", "keyword"])
def test_app_rejects_identity_address_as_tool_name(kind: str, naming: str) -> None:
    app = FastMCPApp("contacts")
    decorator = getattr(app, kind)

    def save() -> str:
        return "saved"

    reserved_name = "__abcdef012345_save"
    with pytest.raises(ValueError, match="reserved for generated tool addresses"):
        if naming == "function":
            save.__name__ = reserved_name
            decorator(save)
        elif naming == "positional":
            decorator(reserved_name)(save)
        else:
            decorator(name=reserved_name)(save)


@pytest.mark.parametrize("kind", ["tool", "ui"])
@pytest.mark.parametrize(
    "name",
    ["save", "__save", "save__contact", "abcdef012345_save", "__zzzzzzzzzzzz_save"],
)
async def test_app_accepts_names_outside_identity_address_pattern(
    kind: str, name: str
) -> None:
    app = FastMCPApp("contacts")

    def save() -> str:
        return "saved"

    getattr(app, kind)(name=name)(save)
    assert [tool.name for tool in await app._list_tools()] == [name]


@pytest.mark.parametrize("as_tool", [False, True])
def test_add_tool_rejects_identity_address_as_tool_name(as_tool: bool) -> None:
    app = FastMCPApp("contacts")

    def save() -> str:
        return "saved"

    save.__name__ = "__abcdef012345_save"
    tool = Tool.from_function(save)
    with pytest.raises(ValueError, match="reserved for generated tool addresses"):
        app.add_tool(tool if as_tool else save)
    assert tool.meta is None
