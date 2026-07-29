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
  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH")"
  printf 'patch_lines=%s\n' "$(wc -l < "$PATCH")"
  printf 'decoder_bytes=%s\n' "$(wc -c < "$DECODER")"
  printf 'inner_bytes=%s\n' "$(wc -c < "$INNER")"
} | tee "$VERIFY"

chmod +x "$DECODER" "$INNER"
exec bash "$INNER"
