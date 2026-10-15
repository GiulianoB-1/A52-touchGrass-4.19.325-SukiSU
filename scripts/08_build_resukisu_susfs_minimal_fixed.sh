#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# The compatibility generator now uses a dedicated outer heredoc terminator
# (RESUKISUWRAPPERPY), so the former rewrite wrapper is no longer needed.
exec "$SCRIPT_DIR/08_build_resukisu_susfs_compat.sh" "$@"
