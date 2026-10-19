#!/usr/bin/env bash
# Phase446AF reusable symbolizer.
# ./scripts/446af_decode_oops.sh /path/vmlinux /path/faddr2line 'geni_i2c_xfer+0x408/0xe1c'
set -Eeuo pipefail
VMLINUX="$1"
FADDR="$2"
PC="$3"
[[ -s "$VMLINUX" ]] || { echo "P446AF DECODE vmlinux missing"; exit 1; }
[[ -f "$FADDR" ]] || { echo "P446AF DECODE kernel faddr2line missing"; exit 1; }
[[ "$PC" =~ ^[A-Za-z_][A-Za-z0-9_]*\+0x[0-9a-fA-F]+(/0x[0-9a-fA-F]+)?$ ]] || {
  echo "P446AF DECODE invalid func+offset: $PC"; exit 2;
}
echo "P446AF DECODE source=$(basename "$VMLINUX") PC=$PC"
if ! readelf -SW "$VMLINUX" | grep -F '.debug_info' >/dev/null; then
  echo "P446AF DECODE WARNING: no DWARF debug_info in vmlinux; source lines unavailable."
fi
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
# A pair from a previous build has a stale symbol size; report rather than silently reuse.
if ! bash "$FADDR" "$VMLINUX" "$PC" >"$tmp" 2>&1 || ! grep -Fq " at " "$tmp"; then
  cat "$tmp"
  if [[ "$PC" == */* ]]; then
    echo "P446AF DECODE NOTE: exact symbol-size mismatch; do not trust old offsets on a changed build."
    PC="$(printf '%s' "$PC" | cut -d/ -f1)"
    echo "P446AF DECODE speculative fallback (symbol+offset only): $PC"
    bash "$FADDR" "$VMLINUX" "$PC" || true
  fi
else
  cat "$tmp"
fi
