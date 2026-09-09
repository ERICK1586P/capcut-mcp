#!/usr/bin/env bash
# Build the capcut-mcp image tagged with the wrapper version (from entry.py) and re-point
# capcut-mcp:local at it.  Claude Desktop keeps using capcut-mcp:local, so no config change.
# Roll back:  docker tag capcut-mcp:<old version> capcut-mcp:local  (then restart Claude Desktop)
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^WRAPPER_VERSION = "\(.*\)"/\1/p' entry.py)
[ -n "$VERSION" ] || { echo "WRAPPER_VERSION not found in entry.py" >&2; exit 1; }
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then GIT_SHA="${GIT_SHA}-dirty"; fi
echo "building capcut-mcp:${VERSION} (git ${GIT_SHA})"
docker build --build-arg WRAPPER_VERSION="${VERSION}" --build-arg GIT_SHA="${GIT_SHA}" \
  -t "capcut-mcp:${VERSION}" "$@" .
docker tag "capcut-mcp:${VERSION}" capcut-mcp:local
echo "tagged capcut-mcp:${VERSION} -> capcut-mcp:local"
docker image ls capcut-mcp --format '  {{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedSince}}'
