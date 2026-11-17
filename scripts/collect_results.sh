#!/usr/bin/env bash
# Copy analysis outputs from the working directory into this repository's results/.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/scripts/env.sh"
ROOT="$DYNOPT_ROOT"
mkdir -p "$REPO/results"
rsync -a --delete --exclude plots --exclude run_grid.log "$ROOT/analysis/" "$REPO/results/"
cp "$ROOT/logs/run_grid.log" "$REPO/results/run_grid.log" 2>/dev/null || true
echo "copied to $REPO/results"
