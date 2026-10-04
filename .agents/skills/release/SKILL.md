---
name: release
description: Cut a FastMCP release end to end. Use when the maintainer says "cut a release", "prep a patch", "ship 4.x.y", or asks what a release would contain. Covers the notes preview, the title pun, the docs changelog PR that must land before the tag, the tag itself, the publish fan-out, and verifying gofastmcp.com actually deployed.
---

# Cutting a release

A release is a tag on the branch that owns the line: `<branch>` below is `main` for
the current major and `release/3.x` for 3.x maintenance. Every step runs against that
branch, including the docs PR, so a maintenance entry lands in the maintenance
branch's own changelog above its newest 3.x entry. The tag triggers `Publish fastmcp-slim to PyPI`;
its success fans out to `fastmcp-tasks`, `fastmcp-remote`, and `fastmcp`, and the
`fastmcp` publish job opens the `published-docs` PR that makes gofastmcp.com reflect
the release. Two hand-maintained docs files mirror every release and must be on the
tagged commit, so the docs PR always lands first. PyPI releases are immutable; a bad
one gets a follow-up patch, never a re-tag.

## Procedure

1. **Preview what's in it.**

   ```bash
   git fetch origin && git log v<prev>..origin/<branch> --format='%h %s'
   gh api -X POST repos/PrefectHQ/fastmcp/releases/generate-notes \
     -f tag_name=v<new> -f target_commitish=<branch> -f previous_tag_name=v<prev> --jq .body \
     > /tmp/generated-notes.md
   uv run scripts/release_contributor_credit.py /tmp/generated-notes.md \
     > /tmp/contributor-credit.json
   ```

   Read the two most recent releases for voice: `gh release list -L 5`, then
   `gh release view <tag>` on each.

   **Review the scan's attribution candidates.** The helper batch-fetches metadata
   only for PRs in the generated notes. It suggests shared credit for human authors
   of the PR commits and final merged commit, plus issue reporters whose issues
   are closed by a maintainer PR, even when their co-author trailer is missing. Human co-authors
   are included even when the PR has no linked issue. It excludes known
   agent identities and bots; a GitHub `User` identity alone does not prove someone
   is human. Maintainer permission checks are reused across PRs.

   Supplemental attribution is for community contributors. Keep the PR author's
   credit and any credit already present, but do not add other maintainers or
   investigate their first-time status. Routine maintainer backports need no fresh
   attribution audit and no request for guidance. Preserve existing community credit.

   Apply the suggested entries to `/tmp/generated-notes.md`, for example
   `Fix issue #123 by @jlowin and @reporter in <PR URL>`. Check the flagged exceptions:
   unknown commit identities, missing reporter co-authorship, and issue closures
   that do not represent a contribution (such as administrative duplicate closure).
   Inspect individual PRs or issues only to resolve these discrepancies, rather than
   re-reading every PR. The helper is read-only; it does not change notes or decide
   who is a new contributor. Check prior contribution history only for people
   listed in `first_time_review`: added contributors and GitHub's first-time
   candidates. Earlier co-authorship and credited issue implementations count too.
   Add verified first-time contributors to `New Contributors` with the resolving
   PR link, and keep returning contributors in the shared change entries.

   **Update both contributor counts after attribution is complete.** Count distinct
   human GitHub accounts across the release entries for the total contributor count,
   and distinct accounts in the final `New Contributors` list for the first-time
   count. Count each person once across multiple PRs or roles, and exclude agents
   and bots. The helper's `total_contributors` is the proposed total; its
   `generated_first_time_count` is only the original baseline, not the final count.
   Resolve unknown identities and opt-outs before finalizing totals. Include the
   final counts in the release introduction (for example, `Thanks to our 12
   contributors, including 3 first-time contributors.`), and update any other
   contributor totals in the notes to agree. These counts and shared attribution
   must appear in both the GitHub release and the docs changelog.
   Follow [Contributor credit](../../../docs/development/contributing.mdx#contributor-credit),
   honor opt-outs, and deduplicate entries. Save the completed generated notes to
   `/tmp/generated-notes.md`.

2. **Propose titles.** Titles are `v<version>: <pun>`, pun on the release's main
   theme from the release preview. Check prior titles with
   `gh release list --repo PrefectHQ/fastmcp --limit 60` to avoid repeats and keep
   the options on brand: two to four words, a familiar phrase bent toward the
   theme ("Cache Me If You Can", "Trust, but Proxy", "Come Back Any Time"),
   and a running family for a major's prereleases ("Fourst Contact", "Group
   Effourt"). Offer several; the maintainer picks. Draft the handwritten notes at the
   same time: one or two sentences for a patch, narrative prose for a point release.
   Get sign-off on both before anything is pushed.

3. **Write the notes file** outside the repository (a temp directory), intro only.
   The release title is the heading, so the file has none.

   Also write `/tmp/release-notes.md` containing that intro followed by the completed
   `/tmp/generated-notes.md`. This is the exact body to publish; use the same completed
   generated notes for the docs entry. Do not regenerate plain GitHub notes at
   publication and discard the added contributor credit.

4. **Docs entries.** Branch `docs/changelog-<new>` from `origin/<branch>` and insert
   both blocks (the last argument is the branch the notes are generated against):

   ```bash
   uv run .agents/skills/release/scripts/changelog_entry.py v<new> v<prev> "<pun>" /tmp/notes.md <branch> --release-notes /tmp/generated-notes.md
   ```

   Validate, and repeat until it reports no errors; a parse error names the file
   and line:col:

   ```bash
   cd docs && npx --yes mint@latest broken-links
   ```

   (`@latest` on purpose: the hosted Mintlify build is always current.) Commit,
   push, open the PR titled `docs: add v<new> changelog entries` with base
   `<branch>` and a one-line body, and merge it. Branch policy needs `--admin` on a maintainer-driven release.
   The docs PR itself appears in the GitHub notes but not in the mirror; that is
   expected.

5. **Tag**, immediately after the docs PR merges. Re-run the step 1 preview first;
   if `<branch>` moved, delete the entry blocks from both docs files, re-run the
   helper (it refuses to insert a label that already exists), and land that as a
   follow-up docs PR before tagging.

   Refresh contributor attribution and both temporary notes files along with the
   preview when new changes enter the release. The docs-only changelog PR can appear
   in the GitHub notes without appearing in its own mirror, as described above.

   ```bash
   gh release create v<new> --target <branch> --title "v<new>: <pun>" \
     --notes-file /tmp/release-notes.md
   ```

   `v<prev>` is the last stable tag on that branch, passed as `previous_tag_name` in
   the notes preview. The compare link at the bottom of the created release must
   read `v<prev>...v<new>`. Verify the published contributor entries and
   `New Contributors` list against the completed notes.

6. **Watch the fan-out.** The slim run is keyed to the tag; the others are
   `workflow_run` events on the default branch, so select them by workflow name.
   The slim run appears within a minute of the tag; the `fastmcp` run is queued the
   moment slim succeeds, so watch slim to completion first and the newest `fastmcp`
   run is the right one.

   ```bash
   gh run watch $(gh run list --event release --branch v<new> --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   gh run watch $(gh run list --workflow "Publish fastmcp to PyPI" --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   ```

   Confirm each package with `curl -fsS https://pypi.org/pypi/<pkg>/<new>/json | jq .info.version`;
   the unversioned endpoint lags a few minutes.

7. **Publish docs** (`main` releases only; maintenance releases skip this step and
   leave gofastmcp.com on the current major). When the `fastmcp` run finishes, its last job has opened
   `Publish FastMCP v<new> docs` against `published-docs`:

   ```bash
   gh pr list --base published-docs --state open --json number,title
   gh pr merge <n> --merge --admin
   ```

   Merging pushes `published-docs`, which runs the `Deploy docs` workflow: it asks
   Mintlify's admin API for a deployment of the tip and waits for the verdict.
   That run is the signal that the site changed; the merge alone proves nothing.

   ```bash
   gh run watch $(gh run list --workflow "Deploy docs" --branch published-docs --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   ```

   A red run prints Mintlify's summary and logs; fix the cause on `main` and
   publish again. If Mintlify's queue is backed up (status.mintlify.com), the run
   waits up to 40 minutes; re-run it with
   `gh workflow run deploy-docs.yml --ref published-docs` once their incident clears.

   Verify through the markdown endpoints, which bypass the HTML edge cache:
   `curl -fsS https://gofastmcp.com/changelog.md | grep -c "<pun>"`, and the same
   for `updates.md` and any page the release touched.

## Publishing docs by hand

Used when a deploy failed and was fixed on `main`, or for a docs change outside a
release. The publication commit must match `main`'s tree exactly.

```bash
git fetch origin main published-docs
git checkout -b publish-docs-<topic> origin/published-docs
git read-tree -m -u origin/main && git commit -m "docs: publish main @ $(git rev-parse --short origin/main)"
git diff --quiet HEAD origin/main && echo "tree matches main"
git push -u origin publish-docs-<topic>
gh pr create --base published-docs --title "docs: publish main to published-docs (<what>)" --body "<one line>"
gh pr merge <n> --merge --admin
```

Then watch the `Deploy docs` run and verify the endpoints as in step 7.

## Template: notes file for a patch

```
`ClientGroup` now reference-counts its context the way `Client` does, so entering a
connected group from a nested block or a concurrent task reuses the existing
connections instead of raising.
```

## Gotchas

- `--generate-notes` copies PR titles verbatim; a title containing `<1`, `{`, or `}`
  breaks MDX, and `__name__` renders as bold. `scripts/changelog_entry.py` wraps
  those in backticks; the validator in step 4 catches anything else.
- Mintlify's GitHub App also deploys on its own, but it only rebuilds files
  changed since the last commit it recorded, and it records commits it failed on
  or skipped. Only the `Deploy docs` run's verdict counts; its API-triggered
  deployment is what brings a page a skipped deploy left stale back in line.
- Always pass the last stable tag as `previous_tag_name` when generating notes;
  otherwise a prerelease tag can truncate the PR list. Publish the completed notes
  with `--notes-file`, rather than replacing them with `--generate-notes`.
- Maintenance releases publish packages and notes but never repoint `published-docs`;
  their changelog entries go on the maintenance branch under the matching major section.

## Check before finishing

Each of these prints a count above zero:
`curl -fsS https://pypi.org/pypi/fastmcp/<new>/json | grep -c '"version"'`,
`curl -fsS https://gofastmcp.com/changelog.md | grep -c "<pun>"`,
`curl -fsS https://gofastmcp.com/updates.md | grep -c "<pun>"`.
