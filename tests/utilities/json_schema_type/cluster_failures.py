"""Cluster real-world schema failure records by exception signature.

Usage
-----
Run the crash test with DUMP_SCHEMA_FAILURES to collect per-failure records,
then run this script to cluster them:

    # 1. Run the crash test with failure dumping enabled:
    RUN_REAL_WORLD_SCHEMA_TEST=1 \\
    OPENAPI_DIRECTORY_PATH=/tmp/openapi-directory \\
    DUMP_SCHEMA_FAILURES=/tmp/schema_failures \\
    uv run pytest tests/utilities/json_schema_type/test_real_world_schemas.py \\
        -m integration -n auto --timeout-method=thread -q

    # 2. Cluster the failures:
    python tests/utilities/json_schema_type/cluster_failures.py

    # 3. (Optional) specify a custom dump directory:
    DUMP_SCHEMA_FAILURES=/my/path python tests/utilities/json_schema_type/cluster_failures.py

Each JSONL record written by the test has the shape:
    {
        "provider": "amazonaws.com",
        "name": "TagKey",
        "bucket": "schema_errors",           # type_errors | schema_errors | timeouts | other_errors
        "error_type": "SchemaError",
        "error_msg": "...",
        "schema": "{...}"                    # JSON-encoded, truncated to 2000 chars
    }

Workflow for fixing a cluster
------------------------------
1. Identify the top cluster(s) by count.
2. Grab the example schema from the cluster output.
3. Reproduce in a unit test: add a test to test_json_schema_type.py that calls
   json_schema_to_type() with that schema and asserts the correct type is returned.
4. Fix the root cause in fastmcp_slim/fastmcp/utilities/json_schema_type.py.
5. Re-run the crash test — confirm the cluster count drops.
6. Ratchet the baseline in tests/utilities/json_schema_type/conftest.py:
   - Lower MAX_TYPE_ERRORS / MAX_SCHEMA_ERRORS to the new actual count.
   - Add a comment with the date and what was fixed.
7. Commit.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

DUMP_DIR = Path(os.environ.get("DUMP_SCHEMA_FAILURES", "/tmp/schema_failures"))

_NORMALIZE_RE = re.compile(r"'[^']{3,}'|\"[^\"]{3,}\"|0x[0-9a-fA-F]+|\b\d+\b")


def normalize(msg: str) -> str:
    head = "\n".join(msg.splitlines()[:3])
    return _NORMALIZE_RE.sub("<X>", head)[:300]


def main() -> None:
    if not DUMP_DIR.exists():
        print(f"No dump directory found at {DUMP_DIR}.")
        print("Run the crash test with DUMP_SCHEMA_FAILURES set first.")
        sys.exit(1)

    records = []
    for f in DUMP_DIR.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))

    if not records:
        print(f"No failure records found in {DUMP_DIR}. All schemas passed!")
        return

    print(f"Total failures: {len(records)}")
    print()

    buckets: collections.Counter[str] = collections.Counter(
        r["bucket"] for r in records
    )
    for k, v in buckets.most_common():
        print(f"  {k}: {v}")
    print()

    clusters: collections.Counter[tuple[str, str, str]] = collections.Counter()
    examples: dict[tuple[str, str, str], dict] = {}
    for r in records:
        key = (r["bucket"], r["error_type"], normalize(r["error_msg"]))
        clusters[key] += 1
        examples.setdefault(key, r)

    print(f"=== Top clusters (of {len(clusters)} total) ===")
    for (bucket, etype, sig), count in clusters.most_common():
        print(f"\n[{count:>4}x] {bucket} / {etype}")
        print(f"       sig: {sig[:150]}")
        ex = examples[(bucket, etype, sig)]
        print(f"       ex : provider={ex['provider']}  name={ex['name']}")
        print(f"       msg: {ex['error_msg'][:200]}")


if __name__ == "__main__":
    main()
