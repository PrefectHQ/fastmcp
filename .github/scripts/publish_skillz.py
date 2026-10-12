# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.2", "atproto==0.0.72"]
# ///
"""Publish the FastMCP authoring skill to skillz.supply as gofastmcp.com."""

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from atproto import Client, IdResolver, models
from atproto.exceptions import AtProtocolError

ROOT = Path(__file__).resolve().parents[2]
DID = "did:plc:fkndnffqehgkbuahbysm3d4w"
COLLECTION = "supply.skillz.skill"
REPO = "https://github.com/PrefectHQ/fastmcp"


def tracked_skills() -> list[str]:
    entries = subprocess.check_output(
        ["git", "ls-files", "--stage", "-z", "--", "skills/fastmcp-server/SKILL.md"],
        cwd=ROOT,
        text=True,
    ).split("\0")
    names = []
    for entry in filter(None, entries):
        info, path = entry.split("\t", 1)
        if not info.startswith(("100644 ", "100755 ")):
            raise ValueError("Skill source must be a regular tracked file")
        source = ROOT / path
        if any(
            part.is_symlink() for part in (source, source.parent, source.parent.parent)
        ):
            raise ValueError("Refusing symlinked skill source")
        names.append(source.parent.name)
    if not names:
        raise ValueError("No tracked authoring skill found")
    return names


def skill_record(name: str) -> dict[str, Any]:
    path = ROOT / "skills" / name / "SKILL.md"
    text = path.read_text()
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"{name}: missing YAML frontmatter")
    metadata = yaml.safe_load(parts[1])
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        raise ValueError(f"{name}: frontmatter name must match directory")
    record = {
        "$type": COLLECTION,
        "name": name,
        "slug": name,
        "summary": metadata.get("description"),
        "body": parts[2].strip() + "\n",
        "repoUrl": f"{REPO}/blob/main/{path.relative_to(ROOT)}",
    }
    for key, limit in (("summary", 1024), ("body", 50000)):
        value = record[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name}: {key} must be 1–{limit} characters")
    for key, limit in (("license", 64), ("compatibility", 500)):
        if key in metadata:
            value = metadata[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name}: invalid {key}")
            record[key] = value
    return record


def publish(
    client: Client, records: list[dict[str, Any]], dry_run: bool = False
) -> int:
    # Guard the entire read/compare/write transaction against concurrent edits.
    commit = client.com.atproto.sync.get_latest_commit({"did": DID}).cid
    existing = {}
    cursor = None
    while True:
        query = {"repo": DID, "collection": COLLECTION, "limit": 100}
        if cursor:
            query["cursor"] = cursor
        page = client.com.atproto.repo.list_records(query)
        for item in page.records:
            existing[item.uri.rsplit("/", 1)[1]] = models.get_model_as_dict(item.value)
        cursor = page.cursor
        if not cursor:
            break

    writes = []
    for record in records:
        name = record["name"]
        rkey = f"fastmcp.{name}"
        old = existing.get(rkey)
        if old and old.get("repoUrl") != record["repoUrl"]:
            raise ValueError(f"{rkey}: existing record belongs to another source")
        value = dict(record)
        value["createdAt"] = (
            old["createdAt"] if old else datetime.now(timezone.utc).isoformat()
        )
        if value == old:
            print(f"{name}: unchanged")
            continue
        action = "update" if old else "create"
        writes.append(
            {
                "$type": f"com.atproto.repo.applyWrites#{action}",
                "collection": COLLECTION,
                "rkey": rkey,
                "value": value,
            }
        )
        print(f"{name}: {'would ' if dry_run else ''}{action}")
    if writes and not dry_run:
        password = os.environ.get("SKILLZ_APP_PASSWORD")
        if not password:
            raise ValueError("SKILLZ_APP_PASSWORD is required")
        client.login(DID, password, fetch_bsky_profile=False)
        if client.com.atproto.server.get_session().did != DID:
            raise ValueError("credential authenticated as the wrong DID")
        client.com.atproto.repo.apply_writes(
            {
                "repo": DID,
                "swapCommit": commit,
                "writes": writes,
            }
        )
        for write in writes:
            print(f"https://skillz.supply/skills/{DID}/{write['rkey']}")
    return len(writes)


def source_is_current() -> bool:
    subprocess.run(
        ["git", "fetch", "--depth=1", "origin", "refs/heads/main"], cwd=ROOT, check=True
    )
    result = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "FETCH_HEAD",
            "--",
            "skills/fastmcp-server",
            ".github/scripts/publish_skillz.py",
            ".github/workflows/publish-skillz.yml",
        ],
        cwd=ROOT,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Could not compare publication source with main")
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") and not args.dry_run:
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise ValueError("Publishing requires the main branch")
        if not source_is_current():
            print("Publication source superseded on main; skipping publish")
            return
    records = [skill_record(name) for name in tracked_skills()]
    document = IdResolver().did.resolve(DID)
    pds = document.get_pds_endpoint() if document else None
    if not pds:
        raise ValueError("Publishing account has no PDS endpoint")
    publish(Client(pds), records, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except AtProtocolError:
        # SDK errors can contain request/response details. Keep credentials out of CI logs.
        raise SystemExit("AT Protocol SDK request failed") from None
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
