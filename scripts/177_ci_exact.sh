#!/usr/bin/env bash
set -Eeuo pipefail

CHUNK_DIR="$PWD/scripts/177_patch_payload_chunks"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
OUT="$PWD/artifacts/a52xq-display-init-recorder-plain"
EXPECTED_PATCH_SHA256="79c00e5e6051097ab451b3d00cfa593dbcb6675013a777ea5caa5d5c6df2e8e9"

mkdir -p "$(dirname "$PATCH")" "$OUT/logs"
{
  printf '%s  %s\n' \
    87ae7a09a96e5bc9549d33fee2ad76227a78a0813dc2751787557970a0aa382c "$CHUNK_DIR/00.txt" \
    8c6c35e83621caefc6653ff70c244c86851c68ba0a6981d9b2c9d3a171d4dd37 "$CHUNK_DIR/01.txt" \
    ad2e50d5e4d5dd48747e6970193f3be2023626aed26520a774cca9e7062cfccb "$CHUNK_DIR/02.txt" \
    c4af4b8ef0c3a709d024aa65b4a6fa8f10285f2d91d3ca1f5af71d8cd8c19e31 "$CHUNK_DIR/03.txt" \
    | sha256sum -c -
  for chunk in "$CHUNK_DIR"/00.txt "$CHUNK_DIR"/01.txt "$CHUNK_DIR"/02.txt "$CHUNK_DIR"/03.txt; do
    test "$(wc -c < "$chunk")" = 2423
  done
  cat "$CHUNK_DIR"/00.txt "$CHUNK_DIR"/01.txt \
      "$CHUNK_DIR"/02.txt "$CHUNK_DIR"/03.txt \
      > "$OUT/logs/trace-patch.gz.b64"
  printf 'payload_bytes=%s\n' "$(wc -c < "$OUT/logs/trace-patch.gz.b64")"
  test "$(wc -c < "$OUT/logs/trace-patch.gz.b64")" = 9692
  base64 --decode "$OUT/logs/trace-patch.gz.b64" > "$OUT/logs/trace-patch.gz"
  gzip -t "$OUT/logs/trace-patch.gz"
  gzip -dc "$OUT/logs/trace-patch.gz" > "$PATCH"
  sha256sum "$PATCH"
  printf 'patch_lines=%s\n' "$(wc -l < "$PATCH")"
} > "$OUT/logs/patch-payload-verification.txt" 2>&1

printf '%s  %s\n' "$EXPECTED_PATCH_SHA256" "$PATCH" | sha256sum -c -
test "$(wc -l < "$PATCH")" = 793

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

old_source = 'gki/common/techpack/display/'
new_source = 'gki/common/drivers/a52_display/'
if text.count(old_source) != 9:
    raise SystemExit(f'expected nine source paths, found {text.count(old_source)}')
text = text.replace(old_source, new_source)

old_object = "'CC      techpack/display/msm/dsi/"
new_object = "'CC      drivers/a52_display/msm/dsi/"
if text.count(old_object) != 6:
    raise SystemExit(f'expected six object paths, found {text.count(old_object)}')
text = text.replace(old_object, new_object)

path.write_text(text)
PY

exec bash scripts/177_ci.sh
