#!/usr/bin/env bash
#
# Update Nurby to the latest code and restart the stack.
#
# Run this on the host that runs Docker Compose.
#   ./scripts/update.sh
#
# It pulls the latest code, rebuilds images, and restarts. Database
# migrations run automatically when the API container starts, so there
# is nothing else to do.
set -euo pipefail

# Move to the repo root regardless of where this is called from.
cd "$(dirname "$0")/.."

echo "[nurby] current version. $(cat VERSION 2>/dev/null || echo unknown)"

echo "[nurby] fetching latest code."
git fetch --all --tags --prune
# Fast-forward only. refuses to clobber local changes. stash or commit them first.
git pull --ff-only

echo "[nurby] updated to version. $(cat VERSION 2>/dev/null || echo unknown)"

echo "[nurby] rebuilding and restarting the stack."
# Pull any prebuilt images, then build local ones, then bring it all up.
docker compose pull --ignore-pull-failures || true
docker compose up -d --build --remove-orphans

echo "[nurby] done. migrations run automatically on API startup."
echo "[nurby] tail logs with. docker compose logs -f api"
