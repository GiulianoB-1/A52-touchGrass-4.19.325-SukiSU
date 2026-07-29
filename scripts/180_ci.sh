#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD_DIR="$PWD/scripts/180_payloads"
AUDIT="$PWD/scripts/180_a52_display_bind_audit.c"
DECODER="$PWD/tools/decode-a52-r180-soft-rs.py"
INNER="$PWD/scripts/180_ci_inner.sh"
VERIFY="$PWD/artifacts/a52xq-display-bindcore-retry/logs/payload-verification.txt"
mkdir -p "$(dirname "$AUDIT")" "$(dirname "$DECODER")" "$(dirname "$VERIFY")"

rebuild() {
  local payload="$1" output="$2" expected="$3"
  tr -d '\r\n' < "$payload" | base64 --decode | gzip -dc > "$output"
  printf '%s  %s\n' "$expected" "$output" | sha256sum -c -
}

{
  rebuild "$PAYLOAD_DIR/audit.gz.b64" "$AUDIT" \
    d903ec559bb1f5483f5b063cb421a4779416295734e417dbc1dabeaeeeb2c3f1
  rebuild "$PAYLOAD_DIR/decoder.gz.b64" "$DECODER" \
    45b86bc28a37cda83ed5ae1ed36449d733976cdd14f0af0dc2f4d9c53840f952
  rebuild "$PAYLOAD_DIR/ci-inner.gz.b64" "$INNER" \
    36ac9914fa0f9f7179c2f6fd52eb9201bfd5668fb9c56cfdd96bf6e3ef5c5770
  printf 'audit_bytes=%s\n' "$(wc -c < "$AUDIT")"
  printf 'decoder_bytes=%s\n' "$(wc -c < "$DECODER")"
  printf 'inner_bytes=%s\n' "$(wc -c < "$INNER")"
} | tee "$VERIFY"

chmod +x "$DECODER" "$INNER"
exec bash "$INNER"
