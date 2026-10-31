from pydantic import Field

from fastmcp.prompts import Prompt
from fastmcp.prompts.base import Message


async def test_validation_alias_takes_precedence_over_alias():
    def total(
        numbers: list[int] = Field(alias="Numbers", validation_alias="Input"),
    ) -> str:
        return f"sum={sum(numbers)}"

    prompt = Prompt.from_function(total)
    assert prompt.arguments is not None
    assert [arg.name for arg in prompt.arguments] == ["Input"]
    result = await prompt.render({"Input": "[1, 2, 3]"})
    assert result.messages == [Message("sum=6")]


async def test_validation_alias_takes_precedence_with_default():
    def total(
        numbers: list[int] = Field(
            default=[7], alias="Numbers", validation_alias="Input"
        ),
    ) -> str:
        return f"sum={sum(numbers)}"

    prompt = Prompt.from_function(total)
    assert (await prompt.render({"Input": "[1, 2]"})).messages == [Message("sum=3")]
    assert (await prompt.render({})).messages == [Message("sum=7")]
