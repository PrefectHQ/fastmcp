"""Independent protocol checks, fixed before trial outputs were generated."""

import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import Client

work = Path(sys.argv[1]).resolve()
case = sys.argv[2]
results: list[dict[str, Any]] = []


def check(name: str, condition: Any, evidence: Any = None) -> None:
    results.append({"check": name, "passed": bool(condition), "evidence": evidence})


def payload(result: Any) -> Any:
    data = result.structured_content or result.data
    if isinstance(data, dict) and set(data) == {"result"}:
        return data["result"]
    return data


async def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / "test.sqlite"
        os.environ["INVENTORY_DB"] = str(db)
        sys.path.insert(0, str(work))
        spec = importlib.util.spec_from_file_location(
            "trial_server", work / "server.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if case == "inventory":
            check("no_database_at_import", not db.exists())
        async with Client(module.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            if case == "catalog":
                tool = tools.get("search_products")
                check("tool_discovered", tool is not None)
                assert tool is not None
                schema = tool.inputSchema
                props = schema.get("properties", {})
                check("required_query", "query" in schema.get("required", []))
                check(
                    "finite_category_schema",
                    props.get("category", {}).get("enum") == ["books", "games"],
                    props.get("category"),
                )
                check(
                    "parameter_descriptions",
                    all(
                        props.get(key, {}).get("description")
                        for key in ["query", "category", "cursor"]
                    ),
                    props,
                )
                first = payload(
                    await client.call_tool("search_products", {"query": "Python"})
                )
                check(
                    "first_page",
                    [x["id"] for x in first.get("items", [])] == ["b1", "b2"]
                    and first.get("complete") is False
                    and bool(first.get("next_cursor")),
                    first,
                )
                second = payload(
                    await client.call_tool(
                        "search_products",
                        {"query": "Python", "cursor": first["next_cursor"]},
                    )
                )
                check(
                    "continuation",
                    [x["id"] for x in second.get("items", [])] == ["b3"]
                    and second.get("complete") is True
                    and second.get("next_cursor") is None,
                    second,
                )
                empty = payload(
                    await client.call_tool("search_products", {"query": "missing"})
                )
                check(
                    "empty_complete",
                    empty.get("items") == [] and empty.get("complete") is True,
                    empty,
                )
                failed = await client.call_tool(
                    "search_products", {"query": "offline"}, raise_on_error=False
                )
                text = " ".join(getattr(x, "text", "") for x in failed.content)
                check(
                    "upstream_error", failed.is_error and "retry" in text.lower(), text
                )
                invalid = await client.call_tool(
                    "search_products",
                    {"query": "Python", "category": "music"},
                    raise_on_error=False,
                )
                check("invalid_category", invalid.is_error)
                prompt = await client.get_prompt(
                    "recommend_products", {"topic": "Python"}
                )
                text = str(prompt)
                check("prompt", "Python" in text and "page" in text.lower(), text)
            else:
                tool = tools.get("transfer_stock")
                check("tool_discovered", tool is not None)
                assert tool is not None
                props = tool.inputSchema.get("properties", {})
                check(
                    "clean_input_schema",
                    set(props) == {"from_sku", "to_sku", "amount"},
                    props,
                )
                check(
                    "integer_amount_schema",
                    props.get("amount", {}).get("type") == "integer",
                )

                def stock() -> dict[str, int]:
                    with sqlite3.connect(db) as conn:
                        return dict(conn.execute("SELECT sku,quantity FROM stock"))

                check("initial_stock", stock() == {"pen": 5, "notebook": 10}, stock())
                success = payload(
                    await client.call_tool(
                        "transfer_stock",
                        {"from_sku": "pen", "to_sku": "notebook", "amount": 2},
                    )
                )
                check(
                    "transfer_result",
                    all(
                        success.get(k) == v
                        for k, v in {
                            "from_sku": "pen",
                            "to_sku": "notebook",
                            "from_quantity": 3,
                            "to_quantity": 12,
                        }.items()
                    ),
                    success,
                )
                check(
                    "transfer_persisted", stock() == {"pen": 3, "notebook": 12}, stock()
                )
                for name, args in [
                    (
                        "insufficient",
                        {"from_sku": "pen", "to_sku": "notebook", "amount": 4},
                    ),
                    (
                        "missing_destination",
                        {"from_sku": "pen", "to_sku": "missing", "amount": 1},
                    ),
                    (
                        "missing_source",
                        {"from_sku": "missing", "to_sku": "pen", "amount": 1},
                    ),
                    ("same_sku", {"from_sku": "pen", "to_sku": "pen", "amount": 1}),
                    ("zero", {"from_sku": "pen", "to_sku": "notebook", "amount": 0}),
                    (
                        "negative",
                        {"from_sku": "pen", "to_sku": "notebook", "amount": -1},
                    ),
                ]:
                    result = await client.call_tool(
                        "transfer_stock", args, raise_on_error=False
                    )
                    check(
                        name + "_error_atomic",
                        result.is_error and stock() == {"pen": 3, "notebook": 12},
                    )
                resource = await client.read_resource("inventory://stock")
                resource_data = json.loads(resource[0].text)
                check(
                    "json_resource",
                    "pen" in str(resource_data) and "12" in str(resource_data),
                    resource_data,
                )
                prompt = await client.get_prompt(
                    "summarize_inventory", {"focus": "low stock"}
                )
                check(
                    "prompt_resource_reference",
                    "inventory://stock" in str(prompt) and "low stock" in str(prompt),
                    str(prompt),
                )
        if case == "inventory":
            async with Client(module.mcp) as client:
                check(
                    "restart_preserves_database",
                    stock() == {"pen": 3, "notebook": 12},
                    stock(),
                )


try:
    asyncio.run(main())
except Exception as exc:
    check("unhandled_exception", False, f"{type(exc).__name__}: {exc}")
functional = [
    result for result in results if result["check"] != "parameter_descriptions"
]
print(
    json.dumps(
        {
            "workspace": str(work),
            "case": case,
            "passed": sum(x["passed"] for x in functional),
            "total": len(functional),
            "checks": functional,
            "diagnostics": [result for result in results if result not in functional],
        },
        indent=2,
    )
)
sys.exit(0 if all(x["passed"] for x in functional) else 1)
