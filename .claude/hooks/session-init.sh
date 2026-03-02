#!/bin/bash
set -e

# Only run in remote/cloud environments
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

command -v gh &> /dev/null && exit 0

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
VERSION=$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
TARBALL="gh_${VERSION#v}_linux_${ARCH}.tar.gz"

echo "Installing gh ${VERSION}..."
TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT
curl -fsSL "https://github.com/cli/cli/releases/download/${VERSION}/${TARBALL}" | tar -xz -C "$TEMP"
cp "$TEMP"/gh_*/bin/gh "$LOCAL_BIN/gh"
chmod 755 "$LOCAL_BIN/gh"

[ -n "$CLAUDE_ENV_FILE" ] && echo "export PATH=\"$LOCAL_BIN:\$PATH\"" >> "$CLAUDE_ENV_FILE"
echo "gh installed: $("$LOCAL_BIN/gh" --version | head -1)"
