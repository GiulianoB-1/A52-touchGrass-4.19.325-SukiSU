#!/usr/bin/env bash
set -Eeuo pipefail

CHUNK_DIR="$PWD/scripts/177_patch_payload_chunks"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
OUT="$PWD/artifacts/a52xq-display-init-recorder-plain"
EXPECTED_PATCH_SHA256="9412b28da19c71e7bc97e767e83ce717e146313d32321c7429e6d4050a4f0d00"

mkdir -p "$(dirname "$PATCH")" "$OUT/logs"
{
  for item in \
    '00.txt 3ae80dacd67c540df6fe2c31521e57699b0a72fdea4986ee8c4effbc07bdd9df' \
    '01.txt fc1829cb8f8fde72419cf134917326cf2367160bd568f85e9008431e52795ae7' \
    '02.txt 24648bc687d4849f43c7624e68f89a091cfc9be46342a921b841651d8a3c9f1e' \
    '03.txt 513ab3fb354264ce2d6e3dc475eb410b2d4e9288d230a143adf954884a05e0b7'; do
    set -- $item
    src="$CHUNK_DIR/$1"
    normal="$OUT/logs/$1.normalised"
    tr -d '\r\n' < "$src" > "$normal"
    test "$(wc -c < "$normal")" = 2432
    printf '%s  %s\n' "$2" "$normal" | sha256sum -c -
  done
  cat "$OUT/logs/00.txt.normalised" "$OUT/logs/01.txt.normalised" \
      "$OUT/logs/02.txt.normalised" "$OUT/logs/03.txt.normalised" \
      > "$OUT/logs/trace-patch.gz.b64"
  printf 'payload_bytes=%s\n' "$(wc -c < "$OUT/logs/trace-patch.gz.b64")"
  test "$(wc -c < "$OUT/logs/trace-patch.gz.b64")" = 9728
  base64 --decode "$OUT/logs/trace-patch.gz.b64" > "$OUT/logs/trace-patch.gz"
  gzip -t "$OUT/logs/trace-patch.gz"
  gzip -dc "$OUT/logs/trace-patch.gz" > "$PATCH"
  sha256sum "$PATCH"
  printf 'patch_lines=%s\n' "$(wc -l < "$PATCH")"
} > "$OUT/logs/patch-payload-verification.txt" 2>&1

printf '%s  %s\n' "$EXPECTED_PATCH_SHA256" "$PATCH" | sha256sum -c -
test "$(wc -l < "$PATCH")" = 796

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
