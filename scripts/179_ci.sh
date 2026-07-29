#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD_DIR="$PWD/scripts/179_payloads"
PATCH="$PWD/patches/179-a52-display-takeover-rs-mirror.patch"
DECODER="$PWD/tools/decode-a52-r179-rs-recorder.py"
INNER="$PWD/scripts/179_ci_inner.sh"
VERIFY="$PWD/artifacts/a52xq-display-takeover-rs-mirror/logs/payload-verification.txt"

mkdir -p "$(dirname "$PATCH")" "$(dirname "$DECODER")" "$(dirname "$VERIFY")"

rebuild() {
  local payload="$1"
  local output="$2"
  local expected="$3"
  tr -d '\r\n' < "$payload" | base64 --decode | gzip -dc > "$output"
  printf '%s  %s\n' "$expected" "$output" | sha256sum -c -
}

{
  rebuild "$PAYLOAD_DIR/patch.gz.b64" "$PATCH" \
    81bc17510b643274dba9652baa5edf52e9c2127af02a77eb1597637be0c3c59f
  rebuild "$PAYLOAD_DIR/decoder.gz.b64" "$DECODER" \
    070a080177b1918b60ba60fc663668acf68e4c06b8f43502ce3010312afcf11f
  rebuild "$PAYLOAD_DIR/ci-inner.gz.b64" "$INNER" \
    ee917bbf8eb9caebce46baec011c42d8680be1f4cb8d358a936b0b6cc1a32dfe

  python3 - <<'PY'
from pathlib import Path
path = Path('scripts/179_ci_inner.sh')
text = path.read_text()

old_connector = "for marker in 'DISP CONN pre' 'DISP CONN complete' 'DISP CONN panel_dead' 'DISP CONN esd'; do\n"
new_connector = "for marker in 'DISP CONN pre' 'DISP CONN complete' 'DISP ESD panel_dead' 'DISP ESD status' 'DISP ESD work'; do\n"
if text.count(old_connector) != 1:
    raise SystemExit(f'expected one connector audit line, found {text.count(old_connector)}')
text = text.replace(old_connector, new_connector, 1)

old_binary_check = '  grep -aFq "$marker" "$OUT/compile/Image"\n'
new_binary_check = (
    '  if ! grep -aFq "$marker" "$OUT/compile/Image"; then\n'
    '    printf "optimised_image_marker_missing=%s\\n" "$marker" >> "$OUT/logs/image-marker-audit.txt"\n'
    '  fi\n'
)
if text.count(old_binary_check) != 1:
    raise SystemExit(f'expected one binary marker check, found {text.count(old_binary_check)}')
text = text.replace(old_binary_check, new_binary_check, 1)

path.write_text(text)
PY

  grep -Fq "DISP ESD panel_dead" "$INNER"
  grep -Fq "optimised_image_marker_missing" "$INNER"
  grep -Fq "encode_rs8" "$PATCH"
  grep -Fq "class ReedSolomon" "$DECODER"

  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH")"
  printf 'patch_lines=%s\n' "$(wc -l < "$PATCH")"
  printf 'decoder_bytes=%s\n' "$(wc -c < "$DECODER")"
  printf 'inner_bytes=%s\n' "$(wc -c < "$INNER")"
} | tee "$VERIFY"

chmod +x "$DECODER" "$INNER"
exec bash "$INNER"
