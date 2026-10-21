"""Collect prompt URI reachability evidence for manual review, not a semantic grade."""

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from mcp.shared.exceptions import McpError

from fastmcp import Client

work = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(work))
spec = importlib.util.spec_from_file_location("trial_server", work / "server.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


async def main() -> None:
    results: list[dict[str, Any]] = []
    for name, server, prompt_name in [
        ("portal", module.mcp, "docs_summarize_article"),
        ("docs", module.docs, "summarize_article"),
    ]:
        async with Client(server) as client:
            prompt = await client.get_prompt(prompt_name, {"slug": "install"})
            text = " ".join(message.content.text for message in prompt.messages)
            candidates = []
            for uri in re.findall(r"article://[A-Za-z0-9/_-]+", text):
                try:
                    resource = await client.read_resource(uri)
                    candidates.append(
                        {
                            "uri": uri,
                            "reachable": True,
                            "content": json.loads(resource[0].text),
                        }
                    )
                except McpError as exc:
                    candidates.append(
                        {"uri": uri, "reachable": False, "error": str(exc)}
                    )
            templates = [
                str(template.uriTemplate)
                for template in await client.list_resource_templates()
            ]
            results.append(
                {
                    "client": name,
                    "status": "unassessed",
                    "prompt": text,
                    "templates": templates,
                    "candidates": candidates,
                }
            )
    print(json.dumps({"workspace": str(work), "checks": results}, indent=2))


asyncio.run(main())
