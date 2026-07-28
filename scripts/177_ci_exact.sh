#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD="$PWD/scripts/177_patch_payload.b64"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
EXPECTED_PATCH_SHA256="40d003f18d0d738f7bb9baaa942b33b54d32d038ad5649ffa91da70201d08c30"

mkdir -p "$(dirname "$PATCH")"
base64 --decode "$PAYLOAD" | gzip -dc > "$PATCH"
printf '%s  %s\n' "$EXPECTED_PATCH_SHA256" "$PATCH" | sha256sum -c -
test "$(wc -l < "$PATCH")" = 817

exec bash scripts/177_ci.sh
