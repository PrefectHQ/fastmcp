#!/usr/bin/env python3
"""Public-text rules for anything posted to PrefectHQ/fastmcp by maintainer agents.

Runs as a pre-tool hook for Claude Code and Codex. Reads the hook payload on
stdin, finds GitHub writes aimed at this repository, and exits 2 with the
violations on stderr so the agent rewrites the text instead of posting it.

    python3 .github/scripts/public_text.py            # hook mode (stdin JSON)
    python3 .github/scripts/public_text.py --check F  # lint a body file as a review
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPOSITORY = "prefecthq/fastmcp"
MAX_WORDS = 120

BANNED = [
    (
        r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?(?:want|next action)(?:\*\*)?\s*:",
        '"Want:" / "Next action:" prefixes',
    ),
    (r"(?im)^\s*(?:\$ |>>> |uv run |pytest )", "command transcripts"),
    (r"(?i)commands actually executed", "command transcripts"),
    (r"\b[0-9a-f]{40}\b", "full 40-character SHAs"),
    (r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "run UUIDs"),
    (
        r"(?i)assignee.eligibility|issue.link gate|contribution gate|ci gate",
        "assignment / CI-gate administration",
    ),
    (
        r"(?i)ai.assisted[^.\n]{0,40}review|not a claim of human review",
        "AI-review disclaimers",
    ),
    (
        r"(?i)four.head|fresh adversarial|independent triage review run",
        "internal review jargon",
    ),
    (r"(?i)backport is (?:not )?authori[sz]ed", "internal policy"),
    (
        r"(?i)does not (?:reject|select) (?:an|this) implementation",
        "repeated disclaimers",
    ),
]

HARNESS_BRANCH = re.compile(
    r"(?:^|[\s:/])(?:refs/heads/)?(claude|codex|cursor|copilot|devin|aider)/[\w./-]+"
)

COMMENT_KINDS = {"review", "comment"}


def visible(body: str) -> str:
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    body = re.sub(r"(?is)<details\b.*?</details>", " ", body)
    body = re.sub(r"(?s)```.*?```", " ", body)
    return re.sub(r"<[^>]+>", " ", body)


def violations(body: str, kind: str) -> list[str]:
    found = []
    words = len(visible(body).split())
    if kind in COMMENT_KINDS and words > MAX_WORDS:
        found.append(
            f"{words} visible words (max {MAX_WORDS}); move detail into <details><summary>Checks</summary>"
        )
    if kind in COMMENT_KINDS and re.search(r"(?m)^#{1,6} ", visible(body)):
        found.append("markdown headers")
    text = re.sub(r"(?s)<!--.*?-->", " ", body)
    for pattern, label in BANNED:
        if re.search(pattern, text) and label not in found:
            found.append(label)
    return found


def branch_violation(name: str | None) -> str | None:
    if name and HARNESS_BRANCH.search(" " + name):
        return f"branch {name!r}: use a plain feature branch name (no claude/, codex/ or other harness prefix)"
    return None


def cwd_repo(cwd: str | None) -> str | None:
    try:
        url = subprocess.run(
            ["git", "-C", cwd or ".", "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    return match.group(1).lower() if match else None


def current_branch(cwd: str | None) -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "-C", cwd or ".", "rev-parse", "--abbrev-ref", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def heredoc(command: str) -> str | None:
    match = re.search(
        r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\s*\1\s*(?:\n|\)|$)",
        command,
        re.DOTALL,
    )
    return match.group(2) if match else None


def option(args: list[str], *names: str) -> str | None:
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if name.startswith("--") and arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


GH_API_VALUE_OPTIONS = {
    "-X",
    "--method",
    "-H",
    "--header",
    "-f",
    "--raw-field",
    "-F",
    "--field",
    "--input",
    "-q",
    "--jq",
    "-t",
    "--template",
    "-p",
    "--preview",
    "--hostname",
    "--cache",
}


def api_endpoint(args: list[str]) -> str:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in GH_API_VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return ""


def read_body(value: str | None, command: str, cwd: str | None) -> str | None:
    if value is None:
        return None
    if value == "-":
        return heredoc(command)
    path = Path(value).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    try:
        return path.read_text()
    except OSError:
        return None


def check_shell(command: str, cwd: str | None) -> list[str]:
    problems = []
    for segment in re.split(r"&&|\|\||;|\n(?=\s*(?:gh|git) )", command):
        try:
            args = shlex.split(segment, comments=False)
        except ValueError:
            args = segment.split()
        if not args:
            continue
        while args and "=" in args[0] and not args[0].startswith("-"):
            args = args[1:]
        if len(args) < 2:
            continue
        tool, sub = args[0].rsplit("/", 1)[-1], args[1:]
        if tool == "git":
            problems += check_git(sub, cwd)
        elif tool == "gh":
            problems += check_gh(sub, segment if "<<" not in command else command, cwd)
    return problems


def check_git(sub: list[str], cwd: str | None) -> list[str]:
    if not sub or cwd_repo(cwd) != REPOSITORY:
        return []
    if sub[0] == "push":
        refs = [a for a in sub[1:] if not a.startswith("-")][1:]
        names = [r.split(":")[-1].lstrip("+") for r in refs] or ["HEAD"]
        names = [current_branch(cwd) if n in {"HEAD", "@"} else n for n in names]
    elif sub[0] in {"checkout", "switch"}:
        names = [option(sub, "-b", "-B", "-c", "-C")]
    elif sub[0] == "branch":
        names = (
            [a for a in sub[1:] if not a.startswith("-")][:1]
            if "-d" not in sub and "-D" not in sub
            else []
        )
    else:
        return []
    return [p for p in map(branch_violation, names) if p]


def check_gh(sub: list[str], command: str, cwd: str | None) -> list[str]:
    repo = (option(sub, "--repo", "-R") or cwd_repo(cwd) or "").lower()
    head = " ".join(sub[:2])
    if head in {"pr review", "pr comment", "issue comment"}:
        kind = "review" if head == "pr review" else "comment"
    elif head in {"pr create", "pr edit", "issue create", "issue edit"}:
        kind = "body"
    elif sub[0] == "api":
        endpoint = api_endpoint(sub[1:])
        named = re.search(r"repos/([^/]+/[^/]+)", endpoint)
        if named and "{owner}" not in endpoint:
            repo = named.group(1).lower()
        writes = option(sub, "-X", "--method") not in {None, "GET"} or any(
            a in sub for a in ("-f", "-F", "--field", "--raw-field", "--input")
        )
        if not writes or not re.search(
            r"/(comments|reviews|pulls|issues)(?:/\d+)*/?$", endpoint
        ):
            return []
        kind = "review" if "/reviews" in endpoint or "/comments" in endpoint else "body"
    else:
        return []
    if repo != REPOSITORY:
        return []
    problems = []
    if kind == "body" and sub[0] == "pr":
        problems += [p for p in [branch_violation(option(sub, "--head", "-H"))] if p]
    body = option(sub, "--body", "-b")
    if body is None:
        body = read_body(
            option(sub, "--body-file", "-F" if sub[0] != "api" else "--input"),
            command,
            cwd,
        )
    if body is None and sub[0] == "api":
        for i, arg in enumerate(sub):
            if (
                arg in {"-f", "-F", "--field", "--raw-field"}
                and i + 1 < len(sub)
                and sub[i + 1].startswith("body=")
            ):
                body = sub[i + 1][5:]
                if body.startswith("@"):
                    body = read_body(body[1:], command, cwd)
    if body is None:
        if (
            head in {"pr review", "pr edit", "issue edit"}
            or "--fill" in sub
            or "--web" in sub
        ):
            return problems
        return problems + [
            "couldn't read the body: write it to a file and pass --body-file so it can be checked"
        ]
    return problems + violations(body, kind)


MCP_BODY_TOOLS = {
    "pull_request_review_write": "review",
    "add_comment_to_pending_review": "review",
    "add_issue_comment": "comment",
    "add_reply_to_pull_request_comment": "comment",
    "update_issue_comment": "comment",
    "create_pull_request": "body",
    "update_pull_request": "body",
    "issue_write": "body",
}


def check_mcp(name: str, args: dict) -> list[str]:
    short = name.rsplit("__", 1)[-1]
    repo = f"{args.get('owner', '')}/{args.get('repo', '')}".lower()
    if repo != REPOSITORY:
        return []
    problems = []
    if short in {
        "create_branch",
        "create_pull_request",
        "push_files",
        "create_or_update_file",
    }:
        problems += [
            p for p in [branch_violation(args.get("branch") or args.get("head"))] if p
        ]
    kind = MCP_BODY_TOOLS.get(short)
    body = args.get("body")
    if kind and isinstance(body, str) and body.strip():
        problems += violations(body, kind)
    return problems


def check_payload(payload: dict) -> list[str]:
    name = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if isinstance(args, str):
        args = {"command": args}
    command = args.get("command") or args.get("cmd")
    if isinstance(command, list):
        command = (
            command[-1]
            if command[:1] in (["bash"], ["sh"], ["zsh"])
            else shlex.join(command)
        )
    if name.startswith("mcp__") or "github" in name.lower() and not command:
        return check_mcp(name, args)
    if isinstance(command, str):
        return check_shell(command, cwd)
    return []


def main() -> int:
    if sys.argv[1:2] == ["--check"]:
        kind = sys.argv[3] if len(sys.argv) > 3 else "review"
        problems = violations(Path(sys.argv[2]).read_text(), kind)
    else:
        try:
            payload = json.load(sys.stdin)
        except ValueError:
            return 0
        problems = check_payload(payload)
    if not problems:
        return 0
    print(
        "Blocked: this breaks the fastmcp GitHub rules "
        "(see .github/scripts/public_text.py):\n- "
        + "\n- ".join(problems)
        + "\nFix it and retry. Public text: the finding and impact once, the requested "
        "change if any, evidence in a collapsed <details>.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
