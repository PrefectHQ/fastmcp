"""
FastMCP Complex inputs Example

Demonstrates validation via pydantic with complex models.
"""

from typing import Annotated

from pydantic import BaseModel, Field

from fastmcp import FastMCP

mcp = FastMCP("Shrimp Tank")


class ShrimpTank(BaseModel):
    class Shrimp(BaseModel):
        name: Annotated[str, Field(max_length=10)]

    shrimp: list[Shrimp]


@mcp.tool
def name_shrimp(
    tank: ShrimpTank,
    # You can use pydantic Field in function signatures for validation.
    extra_names: Annotated[list[str], Field(max_length=10)],
) -> list[str]:
    """List all shrimp names in the tank"""
    return [shrimp.name for shrimp in tank.shrimp] + extra_names
