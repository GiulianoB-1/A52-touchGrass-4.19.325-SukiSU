#!/usr/bin/env bash
# Exploratory fallback for Snapdragon 750G: ARM PMUv3 PC samples without ETM/SPE.
# DO NOT assume a generated profile is good simply because the converter exits 0.
set -Eeuo pipefail
if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <TRAINING-build-vmlinux> <cpu-cycles-perf.data> <out.afdo>" >&2
  exit 2
fi
VMLINUX="$(realpath "$1")"
PERF_DATA="$(realpath "$2")"
OUT="$(realpath -m "$3")"
test -s "$VMLINUX"
test -s "$PERF_DATA"
command -v create_llvm_prof >/dev/null || {
  echo "Install Google's AutoFDO create_llvm_prof >= 0.30.1" >&2
  exit 1
}
create_llvm_prof \
  --use_lbr=false \
  --binary="$VMLINUX" \
  --profile="$PERF_DATA" \
  --format=extbinary \
  --out="$OUT"
test -s "$OUT"
if command -v llvm-profdata >/dev/null; then
  llvm-profdata show --sample "$OUT" > "${OUT}.report.txt"
  test -s "${OUT}.report.txt"
fi
echo "IMPORTANT: Check nonzero function/sample coverage and address mapping first."
echo "A non-LBR PMU sample is an experimental fallback, not equivalent to ETM or SPE."
sha256sum "$VMLINUX" "$PERF_DATA" "$OUT"
