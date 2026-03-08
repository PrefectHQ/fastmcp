#!/usr/bin/env python
"""
Sync this repo with upstream fastmcp (github.com/jlowin/fastmcp).

Use this to keep xsecuremcp2.0 abreast of upstream changes, then make your
own changes and commit.

Usage:
  # From repo root (xsecuremcp2.0/):
  uv run scripts/sync_upstream.py              # fetch + merge upstream/main
  uv run scripts/sync_upstream.py --fetch-only # only fetch, no merge
  uv run scripts/sync_upstream.py --rebase    # rebase on upstream/main instead of merge
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def find_repo_root() -> str:
    """Find git repo root from script location or cwd."""
    start = os.path.dirname(os.path.abspath(__file__))
    path = os.path.abspath(start)
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        path = os.path.dirname(path)
    return os.getcwd()


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command; raise on failure if check=True."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or find_repo_root(),
    )
    if check and result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync with upstream fastmcp")
    ap.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch from upstream, do not merge or rebase",
    )
    ap.add_argument(
        "--rebase",
        action="store_true",
        help="Rebase current branch on upstream/main (default: merge)",
    )
    ap.add_argument(
        "--branch",
        default="main",
        help="Upstream branch to sync from (default: main)",
    )
    args = ap.parse_args()

    repo_root = find_repo_root()
    print("Fetching upstream (fastmcp)...")
    run(["git", "fetch", "upstream"], cwd=repo_root)

    if args.fetch_only:
        print("Done (fetch-only). Update with: git merge upstream/main or git rebase upstream/main")
        return

    # Ensure we're not in the middle of a rebase/merge
    rebase_dir = os.path.join(repo_root, ".git", "rebase-merge")
    if os.path.isdir(rebase_dir):
        status = run(["git", "status", "--porcelain", "-b"], cwd=repo_root, check=False)
        if "rebase" in status.stdout or "REBASE" in status.stdout:
            print(
                "Repository is in the middle of a rebase. Finish with:\n"
                "  git rebase --continue   # or  git rebase --abort",
                file=sys.stderr,
            )
            sys.exit(1)

    upstream_ref = f"upstream/{args.branch}"
    run(["git", "rev-parse", upstream_ref], cwd=repo_root)  # ensure it exists

    if args.rebase:
        print(f"Rebasing current branch on {upstream_ref}...")
        run(["git", "rebase", upstream_ref], cwd=repo_root)
        print("Rebase done.")
    else:
        print(f"Merging {upstream_ref} into current branch...")
        run(["git", "merge", upstream_ref, "--no-edit"], cwd=repo_root)
        print("Merge done.")

    print("Sync complete. Make your changes and commit as usual.")


if __name__ == "__main__":
    main()
