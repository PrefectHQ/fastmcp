"""Insert the release entry into docs/changelog.mdx and docs/updates.mdx.

Usage:
    uv run .agents/skills/release/scripts/changelog_entry.py v4.0.1 v4.0.0 "Come Back Any Time" notes.md
    uv run .agents/skills/release/scripts/changelog_entry.py ... --print   # render only

Reads the maintainer-approved notes file for the intro paragraph and pulls the
PR list from GitHub's generate-notes API, so the docs entry matches what
`gh release create --generate-notes` will append. Each block is inserted above
the newest existing `<Update` in its file. Run from the repository root.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

REPO = "PrefectHQ/fastmcp"


def generate_notes(tag: str, previous: str, target: str) -> str:
    out = subprocess.check_output(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{REPO}/releases/generate-notes",
            "-f",
            f"tag_name={tag}",
            "-f",
            f"target_commitish={target}",
            "-f",
            f"previous_tag_name={previous}",
            "--jq",
            ".body",
        ],
        text=True,
    )
    return out


def linkify(body: str) -> str:
    body = re.sub(r"<!--.*?-->\n?", "", body, flags=re.S)
    body = body.replace("## What's Changed\n", "")
    body = re.sub(
        r"by @([\w-]+) in https://github\.com/" + re.escape(REPO) + r"/pull/(\d+)",
        r"by [@\1](https://github.com/\1) in [#\2](https://github.com/"
        + REPO
        + r"/pull/\2)",
        body,
    )
    body = re.sub(
        r"\* @([\w-]+) made their first contribution in https://github\.com/"
        + re.escape(REPO)
        + r"/pull/(\d+)",
        r"* @\1 made their first contribution in [#\2](https://github.com/"
        + REPO
        + r"/pull/\2)",
        body,
    )
    body = re.sub(
        r"\*\*Full Changelog\*\*: https://github\.com/"
        + re.escape(REPO)
        + r"/compare/(\S+)",
        r"**Full Changelog**: [\1](https://github.com/" + REPO + r"/compare/\1)",
        body,
    )
    return body.strip()


def escape_mdx(text: str) -> str:
    """Backtick-wrap bare `<digit`, `{`, `}` outside code spans; MDX reads them as JSX."""

    def fix(line: str) -> str:
        parts = line.split("`")
        for i in range(0, len(parts), 2):
            parts[i] = re.sub(r"(<\d[^\s`]*)", r"`\1`", parts[i])
            parts[i] = re.sub(r"(\{[^{}]*\})", r"`\1`", parts[i])
            parts[i] = re.sub(r"(?<!`)([{}])(?!`)", r"`\1`", parts[i])
        return "`".join(parts)

    return "\n".join(fix(line) for line in text.splitlines())


def insert_above_newest(path: Path, block: str) -> None:
    text = path.read_text()
    idx = text.find("<Update ")
    if idx < 0:
        raise SystemExit(f"{path}: no <Update block found to insert above")
    if block.splitlines()[0] in text:
        raise SystemExit(f"{path}: an entry with this label already exists")
    path.write_text(text[:idx] + block + "\n" + text[idx:])


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    render_only = "--print" in sys.argv
    if len(args) < 4:
        raise SystemExit(__doc__)
    tag, previous, pun, notes_path = args[:4]
    target = args[4] if len(args) > 4 else "main"
    version = tag.lstrip("v")
    today = dt.date.today()
    intro = " ".join(
        line.strip() for line in open(notes_path).read().split("\n") if line.strip()
    )
    body = escape_mdx(linkify(generate_notes(tag, previous, target)))
    url = f"https://github.com/{REPO}/releases/tag/{tag}"

    changelog = f'''<Update label="{tag}" description="{today.isoformat()}">

**[{tag}: {pun}]({url})**

{intro}

{body}

</Update>
'''
    updates = f'''<Update label="FastMCP {version}" description="{today.strftime("%B %-d, %Y")}" tags={{["Releases"]}}>
<Card
title="FastMCP {tag}: {pun}"
href="{url}"
cta="Read the release notes"
>
{intro}
</Card>
</Update>
'''
    if render_only:
        print(changelog)
        print(updates)
        return
    insert_above_newest(Path("docs/changelog.mdx"), changelog)
    insert_above_newest(Path("docs/updates.mdx"), updates)
    print("inserted entries into docs/changelog.mdx and docs/updates.mdx")


if __name__ == "__main__":
    main()
