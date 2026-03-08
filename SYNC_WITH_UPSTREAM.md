# Keeping abreast of fastmcp and committing changes

This repo (xsecuremcp2.0) tracks **upstream** [fastmcp](https://github.com/jlowin/fastmcp). Use the steps below to stay in sync and commit your work.

## Remotes

- **origin** – your fork: `https://github.com/PureCipher/xsecuremcp2.0.git`
- **upstream** – fastmcp: `https://github.com/jlowin/fastmcp.git`

## 1. Sync with upstream fastmcp

From the repo root:

```bash
# Fetch latest from upstream and merge into current branch
uv run scripts/sync_upstream.py

# Only fetch (no merge)
uv run scripts/sync_upstream.py --fetch-only

# Rebase current branch on upstream/main instead of merge
uv run scripts/sync_upstream.py --rebase
```

If you're in the middle of a rebase, the script will tell you. Finish the rebase first:

```bash
git rebase --continue   # after resolving conflicts and staging
# or
git rebase --abort      # to cancel the rebase
```

Then run `sync_upstream.py` again, or merge manually:

```bash
git fetch upstream
git merge upstream/main --no-edit
```

## 2. Make your changes

Work on your branch as usual. Run tests before committing:

```bash
uv run pytest
```

## 3. Commit and push

```bash
git add -A
git commit -m "Your message"
git push origin <your-branch>
```

---

**Current branch:** `securemcp-core` (and `main`). If you were rebasing `securemcp-core` onto upstream, finish that rebase first, then use the sync script for future updates.
