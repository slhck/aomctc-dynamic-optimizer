#!/usr/bin/env bash
# Copy the AOM CTC a2_2k source clips to the working directory (fast local SSD).
# Usage: scripts/setup_sources.sh [SRC_DIR] [DYNOPT_ROOT]
set -euo pipefail

source "$(dirname "$0")/env.sh"
SRC_DIR="${1:-${DYNOPT_SRC_DIR:?DYNOPT_SRC_DIR is not set in .env}}"
ROOT="${2:-$DYNOPT_ROOT}"

mkdir -p "$ROOT/src"
rsync -a --info=progress2 "$SRC_DIR/"*.y4m "$ROOT/src/"

if [[ -f "$SRC_DIR/a2_2k.md5" ]]; then
  echo "Verifying checksums..."
  (cd "$ROOT/src" && md5sum --quiet --ignore-missing -c "$SRC_DIR/a2_2k.md5") && echo "OK"
fi
