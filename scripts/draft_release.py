"""Draft a GitHub release for each changelog entry a push adds.

The changelog PR (`docs: add vX changelog entries`) is the reviewed source of a
release's title and notes. When it lands, this script turns each new
`<Update label="vX">` block in docs/changelog.mdx into a *draft* GitHub release.
A draft creates no tag and triggers nothing; a maintainer still publishes it,
and the `pypi` environment still waits for approval before anything reaches PyPI.

Usage (from the repository root, with GH_TOKEN allowed to write releases):
    uv run scripts/draft_release.py BEFORE_SHA AFTER_SHA [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass

REPO = "PrefectHQ/fastmcp"
CHANGELOG = "docs/changelog.mdx"

BLOCK = re.compile(
    r'<Update label="(?P<tag>v\d+\.\d+\.\d+[^"]*)"[^>]*>\n(?P<body>.*?)\n</Update>',
    re.S,
)
TITLE = re.compile(r"^\*\*\[(?P<title>v[^\]]+)\]\([^)]*\)\*\*\s*$", re.M)
PRERELEASE = re.compile(r"(?i)\d(?:a|b|rc|alpha|beta|dev)\d*")


@dataclass(frozen=True)
class Entry:
    tag: str
    title: str
    body: str

    @property
    def prerelease(self) -> bool:
        return bool(PRERELEASE.search(self.tag))


def blocks(changelog: str) -> dict[str, str]:
    return {match["tag"]: match["body"] for match in BLOCK.finditer(changelog)}


def parse(tag: str, block: str) -> Entry:
    title = TITLE.search(block)
    if title is None:
        raise ValueError(f"{tag}: changelog entry has no release title")
    return Entry(tag=tag, title=title["title"], body=release_body(block[title.end() :]))


def entries(changelog: str) -> dict[str, Entry]:
    return {tag: parse(tag, block) for tag, block in blocks(changelog).items()}


def release_body(text: str) -> str:
    """Undo the changelog's MDX link formatting so GitHub renders the notes natively."""
    repo = re.escape(REPO)
    text = re.sub(r"\[@([\w-]+)\]\(https://github\.com/[\w-]+\)", r"@\1", text)
    text = re.sub(
        rf"\[#\d+\]\((https://github\.com/{repo}/pull/\d+)\)",
        r"\1",
        text,
    )
    text = re.sub(
        rf"\[[^\]]+\]\((https://github\.com/{repo}/compare/[^)]+)\)",
        r"\1",
        text,
    )
    text = text.strip()
    heading = text.find("\n### ")
    if heading >= 0:
        text = text[:heading].rstrip() + "\n\n## What's Changed" + text[heading:]
    elif text.startswith("### "):
        text = "## What's Changed\n" + text
    return text + "\n"


def new_entries(before: str, after: str) -> list[Entry]:
    old = blocks(before)
    return [parse(tag, block) for tag, block in blocks(after).items() if tag not in old]


def git_show(sha: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    # A missing file (first commit, or a zero BEFORE sha) has no entries.
    return result.stdout if result.returncode == 0 else ""


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", "api", *args], capture_output=True, text=True, encoding="utf-8"
    )


def branch_exists(branch: str) -> bool:
    """Only a confirmed 404 means absent; any other failure stops the draft."""
    result = gh(f"repos/{REPO}/branches/{branch}", "--jq", ".name")
    if result.returncode == 0:
        return True
    if "HTTP 404" in result.stderr:
        return False
    raise SystemExit(f"Could not look up {branch}: {result.stderr.strip()}")


def release_branch(tag: str) -> str | None:
    """Maintenance lines release from their own branch, not from main."""
    major, minor = tag.lstrip("v").split(".")[:2]
    for branch in (f"release/{major}.{minor}", f"release/{major}.x"):
        if branch_exists(branch):
            return branch
    return None


def target_for(tag: str, pushed_sha: str) -> str:
    branch = release_branch(tag)
    if branch is None:
        return pushed_sha
    result = gh(f"repos/{REPO}/commits/{branch}", "--jq", ".sha")
    result.check_returncode()
    return result.stdout.strip()


def existing_tags() -> set[str]:
    # Drafts are invisible to the tags endpoint, so list releases (drafts included).
    result = gh(
        f"repos/{REPO}/releases?per_page=100", "--paginate", "--jq", ".[].tag_name"
    )
    result.check_returncode()
    return set(result.stdout.split())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    added = new_entries(
        git_show(args.before, CHANGELOG), git_show(args.after, CHANGELOG)
    )
    if not added:
        print("No new changelog entries.")
        return
    taken = existing_tags()
    for entry in added:
        if entry.tag in taken:
            print(f"{entry.tag}: a release or draft already exists; leaving it alone.")
            continue
        target = target_for(entry.tag, args.after)
        print(f"{entry.tag}: drafting '{entry.title}' at {target}")
        if args.dry_run:
            print(entry.body)
            continue
        result = gh(
            "-X",
            "POST",
            f"repos/{REPO}/releases",
            "-f",
            f"tag_name={entry.tag}",
            "-f",
            f"target_commitish={target}",
            "-f",
            f"name={entry.title}",
            "-f",
            f"body={entry.body}",
            "-F",
            "draft=true",
            "-F",
            f"prerelease={str(entry.prerelease).lower()}",
            "--jq",
            ".html_url",
        )
        if result.returncode != 0:
            raise SystemExit(f"{entry.tag}: could not create draft: {result.stderr}")
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
