#!/usr/bin/env bash
set -Eeuo pipefail

CHUNK_DIR="$PWD/scripts/177_patch_payload_chunks"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
OUT="$PWD/artifacts/a52xq-display-init-recorder-plain"
EXPECTED_PATCH_SHA256="9412b28da19c71e7bc97e767e83ce717e146313d32321c7429e6d4050a4f0d00"

mkdir -p "$(dirname "$PATCH")" "$OUT/logs"
{
  for item in \
    '00.txt 2e2e8773465271521302902aeedd3c0779c3eba750e191881eb0649654402a39' \
    '01.txt 2cc8aa74d1080a38f3deabaa790ba6cec24accfe542d9747c3f4c6eda00699ba' \
    '02.txt 640c2ce3842ecb8120e14494bb23b5d2e4a1c68700c2a67bab2ba55a04e36731' \
    '03.txt 3799c05943d31375851b3e0680f61fcde892d96272edad98454b641589fd69e0' \
    '04.txt 9e18d44f8df3abe72ac2c67a42919f8acbf90590acdb424ba609d0086c7cfc47' \
    '05.txt 9f0a5026d1a78d096b73ff97f3c785a9fd807999a36decb7a89e7fa851477945' \
    '06.txt b62056e0d393a7969bfac6c60295eb3fb6e78228f6955196e9326cdb90ea1f82' \
    '07.txt da48f50560314b78ef184d7910d5b5a8026164cae8227997a3795e457c16180a'; do
    set -- $item
    src="$CHUNK_DIR/$1"
    normal="$OUT/logs/$1.normalised"
    tr -d '\r\n' < "$src" > "$normal"
    test "$(wc -c < "$normal")" = 1216
    printf '%s  %s\n' "$2" "$normal" | sha256sum -c -
  done
  cat "$OUT/logs/00.txt.normalised" "$OUT/logs/01.txt.normalised" \
      "$OUT/logs/02.txt.normalised" "$OUT/logs/03.txt.normalised" \
      "$OUT/logs/04.txt.normalised" "$OUT/logs/05.txt.normalised" \
      "$OUT/logs/06.txt.normalised" "$OUT/logs/07.txt.normalised" \
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
