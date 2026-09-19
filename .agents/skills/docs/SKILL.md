---
name: docs
description: Write or revise a page under docs/ for gofastmcp.com. Use when adding a feature's documentation, fixing a docs issue, reworking a page, or auditing pages for accessibility and consistency. Covers the page shape, the voice, the checks that run in CI, and how a merged page reaches the live site.
---

# FastMCP docs

The docs are a Mintlify site built from `docs/`. A page exists only when `docs/docs.json` lists it, and a feature exists only when a page documents it. `docs/python-sdk/**` is generated from docstrings by a bot; edit the docstring, not the page.

## Procedure

1. Find the owning page before creating one. `rg -l "<feature>" docs --glob '!docs/python-sdk/**' --glob '!docs/v2/**' --glob '!docs/v3/**'`. Extend it when the feature belongs to that page's subject; add a page when it has its own.
2. Read two neighboring pages in the same `docs.json` group and match their section order and heading depth.
3. Write the page with the template below. Explain why the feature exists before showing how, and keep the first code block small enough to read without scrolling.
4. Make every code block runnable: imports present, names defined, one idea per block. Pull intermediate values into named variables instead of nesting calls.
5. Run the checks in **Verify** and fix what they report.
6. Open a PR on `main`. Merged is not live: the site serves `published-docs`. A stable release publishes automatically; for a docs-only change, follow "Publishing docs by hand" in `.agents/skills/release/SKILL.md` and watch the `Deploy docs` run.

## Template

```mdx
---
title: Feature Name
sidebarTitle: Feature
description: One sentence a search result can show.
icon: some-fontawesome-icon
---

<VersionBadge version="4.1.0" />

One paragraph on the problem this solves and when to reach for it.

## Basic usage

Explain what the example does, then show it.

```python
from fastmcp import FastMCP

mcp = FastMCP("Demo")


@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

## Configuration

One subsection per option that changes behavior, each with a sentence on the default and an example of the override.

## How it works

Only when the mechanism affects what the reader should do. Put a one-sentence text description before any diagram.
```

## Voice

Second person, present tense, plain assertions. Say what the feature does and what the reader should do; leave out marketing adjectives and reassurance. The first sentence of each section carries its point. Prose holds the important information; code comments hold none of it. Wrap bare `{}` in backticks, since Mintlify reads them as JSX.

## Accessibility

Every `<img>` and `![]()` gets alt text that says what the picture shows. Decorative media gets `aria-hidden="true"`. Headings step one level at a time. Link text names the destination. Tables have a header row. Code fences carry a language. Diagrams get a text description. Custom CSS keeps text at 4.5:1 contrast and animations behind `prefers-reduced-motion`.

## Verify

```bash
cd docs && npx --yes mint@latest broken-links
uv run pytest tests/docs -n 0
```

The first catches dead links and MDX parse errors and names the file and line. The second parses every Python block in `docs/` and resolves every `fastmcp` import. A block that must not run takes `test="skip"` on its fence.

For a visual change, run `npx --yes mint@latest dev` in `docs/` and look at the page at 1280px and 390px, light and dark.

## Gotchas

- `VersionBadge` marks when a feature arrived. Leave old badges alone; they are history, not staleness.
- `docs/v2/` and `docs/v3/` are frozen snapshots of earlier majors. Maintenance releases add changelog entries there and nothing else.
- The version picker label lives in `docs/docs.json` under `navigation.versions[0].version` and is bumped by hand.
- `mcp_types` is the real name of the protocol types package in 4.x.
