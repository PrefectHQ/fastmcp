---
emoji: 🏷️
description: Pilot GitHub Agentic Workflows label triage using safe label outputs.
on:
  workflow_dispatch:
    inputs:
      issue_number:
        description: Issue or pull request number to triage with the gh-aw pilot
        required: true
        type: number
permissions:
  contents: read
  issues: read
  pull-requests: read
engine: claude
secrets:
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY_FOR_CI }}
strict: true
network:
  allowed: [defaults, github]
tools:
  github:
    mode: gh-proxy
    toolsets: [default]
safe-outputs:
  add-labels:
    target: "${{ inputs.issue_number }}"
    max: 5
    allowed:
      - auth
      - "breaking change"
      - bug
      - cli
      - client
      - contrib
      - dependencies
      - documentation
      - "DON'T MERGE"
      - enhancement
      - feature
      - high-priority
      - http
      - invalid
      - low-priority
      - "needs more info"
      - openapi
      - security
      - server
      - tests
      - too-long
    blocked:
      - missing-issue-link
      - bypass-issue-check
      - trusted-contributor
      - "prs welcome"
  remove-labels:
    target: "${{ inputs.issue_number }}"
    max: 3
    allowed:
      - auth
      - "breaking change"
      - bug
      - cli
      - client
      - contrib
      - dependencies
      - documentation
      - "DON'T MERGE"
      - enhancement
      - feature
      - high-priority
      - http
      - invalid
      - low-priority
      - "needs more info"
      - openapi
      - security
      - server
      - tests
      - too-long
    blocked:
      - missing-issue-link
      - bypass-issue-check
      - trusted-contributor
      - "prs welcome"
  add-comment:
    target: "${{ inputs.issue_number }}"
    max: 1
---

# Marvin Label Triage gh-aw Pilot

## Task

Triage issue or pull request #${{ inputs.issue_number }} for FastMCP, a Python framework for building Model Context Protocol servers and clients.

This is a manual pilot for GitHub Agentic Workflows. The existing Marvin Label Triage workflow remains the production fallback; use this workflow only to verify whether gh-aw safe outputs can preserve the repository's label safety contract.

## Context to read

1. List the repository's current labels.
2. Read issue or pull request #${{ inputs.issue_number }} and its comments.
3. If the item mentions related issues or pull requests, read only the directly relevant linked items.

Use GitHub read tools only for investigation. Do not attempt direct GitHub mutations; all visible actions must be requested through the configured safe outputs.

## Label safety contract

- Add or remove labels only through the configured `add-labels` and `remove-labels` safe outputs.
- Only use labels that already exist in this repository and are permitted by the safe-output allow list.
- Never add or remove these protected control labels: `missing-issue-link`, `bypass-issue-check`, `trusted-contributor`, `prs welcome`.
- Prefer additive labeling. Remove a label only when it is clearly wrong for this item.
- If no label change is safe or useful, call `noop` with a short explanation.

## Labeling guidelines

Apply 2-5 labels total in typical cases.

### Core category

Apply exactly one core category unless the item is too ambiguous:

- `bug`: broken functionality or PRs that fix bugs.
- `enhancement`: improvements to existing behavior, internal tooling, workflow improvements, minor new capabilities.
- `feature`: major headline functionality worthy of a release announcement.
- `documentation`: significant user-facing docs, examples, or guide changes.

If unsure between `feature` and `enhancement`, choose `enhancement`. If a PR fixes a bug, use `bug` rather than `enhancement`.

### Optional labels

- `breaking change`: backward-incompatible behavior.
- `high-priority`: critical bugs, security issues, or blockers.
- `low-priority`: nice-to-have, cosmetic, or edge-case work.
- `needs more info`: missing reproduction steps, error messages, or clear expected/actual behavior.
- `invalid`: spam, off-topic, or nonsensical reports.
- `too-long`: issue or PR is much more verbose than the contributor guidelines require.
- Area labels when thematically central: `cli`, `client`, `server`, `auth`, `openapi`, `http`, `contrib`, `tests`, `security`.
- `dependencies`: dependabot PRs or issues specifically about package updates.
- `DON'T MERGE`: only if a PR author explicitly says it is not ready.

When applying `too-long`, you may also request one `add-comment` with this text, or a very close variant:

> Thanks for the report. This issue goes beyond what our contributor guidelines ask for — we just need a short problem description and an MRE. Please see our [contributing guidelines](https://github.com/PrefectHQ/fastmcp/blob/main/CONTRIBUTING.md) and condense this issue. We'll triage it once it's trimmed down.

## No-op criteria

Call `noop` instead of adding labels when:

- The item already has the correct labels.
- The item cannot be classified confidently from available context.
- The only plausible labels are outside the safe-output allow list.
- Applying a label would violate the protected-label contract.
