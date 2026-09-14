"""Run Pi with only this example's Hue MCP tools."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Read-only check: discover the Hue tools and inspect light state and capabilities. Do not change any lights.",
    )
    parser.add_argument(
        "--env-file", type=Path, help="Existing dotenv file with bridge credentials"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit Pi's JSON event stream for a tool-call audit",
    )
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi/agent"))
    adapter = agent_dir / "npm/node_modules/pi-mcp-adapter/index.ts"
    if not adapter.is_file():
        parser.error("Install the adapter first: pi install npm:pi-mcp-adapter")
    command = ["run", "--directory", str(project)]
    if args.env_file is not None:
        command.extend(["--env-file", str(args.env_file.resolve())])
    command.append("smart-home")
    config = {
        "mcpServers": {
            "smart-home": {"command": "uv", "args": command, "lifecycle": "eager"}
        }
    }
    with tempfile.TemporaryDirectory(prefix="hue-pi-") as directory:
        extension = Path(directory) / "lights.ts"
        extension.write_text(
            f"import {{ createMcpAdapter }} from {json.dumps(str(adapter))};\n"
            f"export default createMcpAdapter({{ config: {json.dumps(config)} }});\n"
        )
        pi_args = [
            "pi",
            "--no-session",
            "--no-extensions",
            "--extension",
            str(extension),
            "--no-context-files",
            "--no-skills",
            "--no-prompt-templates",
            "--no-builtin-tools",
            "--tools",
            "mcp",
            "--print",
        ]
        if args.json:
            pi_args.extend(["--mode", "json"])
        pi_args.append(args.prompt)
        subprocess.run(pi_args, cwd=directory, check=True)


if __name__ == "__main__":
    main()
