#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/09_fix_susfs_v1_5_5_a52xq_resolver_v3.sh" "$@"
