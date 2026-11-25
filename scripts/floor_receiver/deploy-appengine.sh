#!/usr/bin/env bash
# Deploy the floor receiver to App Engine in the sandbox, from the repo root.
set -euo pipefail
project=prefect-sbx-natenowack
here=$(cd "$(dirname "$0")" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp "$here/app.yaml" "$here/main.py" "$here/../triage_floor_feed.py" "$stage/"
: > "$stage/requirements.txt"
gcloud app deploy "$stage/app.yaml" --project "$project" --quiet
