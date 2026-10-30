#!/usr/bin/env bash
set -euo pipefail

revision=${1:-main}
repository=${2:-https://github.com/PrefectHQ/fastmcp.git}
manifest=${3:-"$(dirname "$0")/../workflows/stack-skills.json"}
if [[ $revision != main && ! $revision =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Expected main or a full Git commit SHA' >&2
  exit 2
fi
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
jq -er '[.common[], .roles[][]] | unique[]' "$manifest" > "$scratch/paths"
git init --quiet "$scratch/source"
git -C "$scratch/source" remote add origin "$repository"
git -C "$scratch/source" config remote.origin.promisor true
git -C "$scratch/source" config remote.origin.partialclonefilter blob:none
git -C "$scratch/source" fetch --quiet --filter=blob:none --depth=1 origin "$revision"
base_sha=$(git -C "$scratch/source" rev-parse 'FETCH_HEAD^{commit}')
if [[ $revision != main && $base_sha != "$revision" ]]; then
  echo 'Fetched revision does not match requested commit' >&2
  exit 1
fi
git -C "$scratch/source" config core.sparseCheckout true
sed 's|^|/|' "$scratch/paths" > "$scratch/source/.git/info/sparse-checkout"
git -C "$scratch/source" -c advice.detachedHead=false checkout --quiet --detach "$base_sha"
printf '{}\n' > "$scratch/files.json"
while IFS= read -r path; do
  if [[ ! $path =~ ^[a-zA-Z0-9_./-]+$ || $path == /* || $path == *..* ]]; then
    echo "Invalid instruction path: $path" >&2
    exit 1
  fi
  mode=$(git -C "$scratch/source" ls-tree "$base_sha" -- "$path" | cut -d ' ' -f 1)
  if [[ $mode != 100644 && $mode != 100755 ]]; then
    echo "Missing regular instruction file: $path at $base_sha" >&2
    exit 1
  fi
  git -C "$scratch/source" show "$base_sha:$path" > "$scratch/content"
  size=$(wc -c < "$scratch/content")
  if ((size == 0 || size > 100000)); then
    echo "Instruction file is empty or exceeds 100 KB: $path" >&2
    exit 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    digest=$(sha256sum "$scratch/content" | cut -d ' ' -f 1)
  else
    digest=$(shasum -a 256 "$scratch/content" | cut -d ' ' -f 1)
  fi
  jq --arg path "$path" --arg digest "$digest" --rawfile content "$scratch/content" \
    '.[$path] = {sha256:$digest,content:$content}' "$scratch/files.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/files.json"
done < "$scratch/paths"
jq -n --arg base "$base_sha" --slurpfile manifest "$manifest" --slurpfile files "$scratch/files.json" '
  $manifest[0] as $m | $files[0] as $f |
  $m.roles | with_entries(
    .key = (.key + "_instructions") |
    .value = {repository:"PrefectHQ/fastmcp",base_sha:$base,
      files:([$m.common[], .value[]] | unique | map({key:.,value:$f[.]}) | from_entries)}
  )'
