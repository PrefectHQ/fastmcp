"""Protocol acceptance for the 3.4.11 portal task, fixed before model runs."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from mcp.shared.exceptions import McpError

from fastmcp import Client

work = Path(sys.argv[1]).resolve()
checks: list[dict[str, Any]] = []


def check(name: str, passed: Any, evidence: Any = None) -> None:
    checks.append({"check": name, "passed": bool(passed), "evidence": evidence})


def payload(result: Any) -> Any:
    data = result.structured_content or result.data
    return (
        data["result"] if isinstance(data, dict) and set(data) == {"result"} else data
    )


def text(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


async def main() -> None:
    sys.path.insert(0, str(work))
    spec = importlib.util.spec_from_file_location("trial_server", work / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    async with Client(module.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        check(
            "namespaced_tools",
            {"docs_status", "ops_status", "docs_get_article"} <= set(tools),
            sorted(tools),
        )
        for team in ("docs", "ops"):
            result = payload(await client.call_tool(f"{team}_status", {}))
            check(team + "_status", result == {"service": team, "ready": True}, result)
        article = payload(
            await client.call_tool("docs_get_article", {"slug": "install"})
        )
        expected = {
            "slug": "install",
            "title": "Install the client",
            "body": "Run pip install example-client.",
        }
        check("article_tool", article == expected, article)
        missing = await client.call_tool(
            "docs_get_article", {"slug": "missing"}, raise_on_error=False
        )
        check(
            "helpful_missing_tool_error",
            missing.is_error
            and "missing" in text(missing).lower()
            and "install" in text(missing).lower(),
            text(missing),
        )
        fault = await client.call_tool(
            "docs_get_article", {"slug": "broken"}, raise_on_error=False
        )
        check(
            "masked_internal_tool_error",
            fault.is_error
            and "synthetic-test-password" not in text(fault)
            and "db.internal" not in text(fault),
            text(fault),
        )
        templates = await client.list_resource_templates()
        uris = [str(t.uriTemplate) for t in templates]
        check("namespaced_resource_template", "article://docs/{slug}" in uris, uris)
        resource = await client.read_resource("article://docs/install")
        check(
            "resource_json", json.loads(resource[0].text) == expected, resource[0].text
        )
        for slug in ("missing", "broken"):
            try:
                result = await client.read_resource(f"article://docs/{slug}")
                check(slug + "_resource_error", False, str(result))
            except McpError as exc:
                message = str(exc)
                valid = (
                    ("missing" in message.lower() and "install" in message.lower())
                    if slug == "missing"
                    else (
                        "synthetic-test-password" not in message
                        and "db.internal" not in message
                    )
                )
                check(slug + "_resource_error", valid, message)
        prompt = await client.get_prompt("docs_summarize_article", {"slug": "install"})
        check(
            "namespaced_prompt",
            "install" in str(prompt) and "read" in str(prompt).lower(),
            str(prompt),
        )

        @module.docs.tool
        def late_added() -> str:
            return "registered-after-start"

        names = {t.name for t in await client.list_tools()}
        check("dynamic_late_discovery", "docs_late_added" in names, sorted(names))
        check(
            "dynamic_late_call",
            payload(await client.call_tool("docs_late_added", {}))
            == "registered-after-start",
        )
    for team in ("docs", "ops"):
        async with Client(getattr(module, team)) as client:
            check(
                team + "_independent",
                payload(await client.call_tool("status", {}))
                == {"service": team, "ready": True},
            )
    check("readme_exists", (work / "README.md").exists())


try:
    asyncio.run(main())
except Exception as exc:
    check("unhandled_exception", False, f"{type(exc).__name__}: {exc}")
print(
    json.dumps(
        {
            "workspace": str(work),
            "case": "composition",
            "passed": sum(c["passed"] for c in checks),
            "total": len(checks),
            "checks": checks,
        },
        indent=2,
    )
)
sys.exit(0 if all(c["passed"] for c in checks) else 1)
