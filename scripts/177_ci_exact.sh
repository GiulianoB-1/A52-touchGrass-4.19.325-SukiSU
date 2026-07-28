#!/usr/bin/env bash
set -Eeuo pipefail

CHUNK_DIR="$PWD/scripts/177_patch_payload_chunks"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
OUT="$PWD/artifacts/a52xq-display-init-recorder-plain"
EXPECTED_PATCH_SHA256="04d57ae9bca9bdb8119ab2a1d6632690cb520e79454f339423427af887d292cb"

mkdir -p "$(dirname "$PATCH")" "$OUT/logs"
{
  printf '%s  %s\n' \
    3d2f26dc8eea494e9b64baa6986b77c1051387cea14aea033e57d1af1213ab8f "$CHUNK_DIR/00.txt" \
    22442d54dc8dee9a4ec8e96d124c070e2e5d89b6a0712d8da9d0bfcd1934f024 "$CHUNK_DIR/01.txt" \
    9e0e18839652631288af021d1983eacb733fc5060d8ef3150f2d997d871c81bf "$CHUNK_DIR/02.txt" \
    a00a7cf3a4db454767ecda0ef04316bc9142d8996e713d4abd5e6191b91f359a "$CHUNK_DIR/03.txt" \
    | sha256sum -c -
  for chunk in "$CHUNK_DIR"/00.txt "$CHUNK_DIR"/01.txt "$CHUNK_DIR"/02.txt "$CHUNK_DIR"/03.txt; do
    test "$(wc -c < "$chunk")" = 2472
  done
  cat "$CHUNK_DIR"/00.txt "$CHUNK_DIR"/01.txt \
      "$CHUNK_DIR"/02.txt "$CHUNK_DIR"/03.txt \
      > "$OUT/logs/trace-patch.gz.b64"
  printf 'payload_bytes=%s\n' "$(wc -c < "$OUT/logs/trace-patch.gz.b64")"
  test "$(wc -c < "$OUT/logs/trace-patch.gz.b64")" = 9888
  base64 --decode "$OUT/logs/trace-patch.gz.b64" > "$OUT/logs/trace-patch.gz"
  gzip -t "$OUT/logs/trace-patch.gz"
  gzip -dc "$OUT/logs/trace-patch.gz" > "$PATCH"
  sha256sum "$PATCH"
  printf 'patch_lines=%s\n' "$(wc -l < "$PATCH")"
} > "$OUT/logs/patch-payload-verification.txt" 2>&1

printf '%s  %s\n' "$EXPECTED_PATCH_SHA256" "$PATCH" | sha256sum -c -
test "$(wc -l < "$PATCH")" = 816

python3 - <<'PY'
from pathlib import Path

path = Path('scripts/177_ci.sh')
text = path.read_text()

old_check = 'git -C gki/common apply --check "$PATCH"\n'
new_check = (
    'git -C gki/common apply --check --verbose "$PATCH" 2>&1 | '
    'tee "$OUT/logs/trace-patch-check.log"\n'
)
if text.count(old_check) != 1:
    raise SystemExit(f'expected one trace patch check, found {text.count(old_check)}')
text = text.replace(old_check, new_check, 1)

old_prefix = "'CC      techpack/display/msm/dsi/"
new_prefix = "'CC      drivers/a52_display/msm/dsi/"
if text.count(old_prefix) != 6:
    raise SystemExit(f'expected six old object paths, found {text.count(old_prefix)}')
text = text.replace(old_prefix, new_prefix)

path.write_text(text)
PY

exec bash scripts/177_ci.sh
