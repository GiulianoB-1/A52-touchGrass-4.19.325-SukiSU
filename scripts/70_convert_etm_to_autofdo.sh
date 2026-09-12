#!/usr/bin/env bash
set -Eeuo pipefail

VMLINUX="${1:-}"
TEXT_PERF="${2:-}"
OUT_PROFILE="${3:-autofdo.prof}"

if [ -z "$VMLINUX" ] || [ -z "$TEXT_PERF" ]; then
  echo "usage: $0 <vmlinux> <simpleperf-injected-text-profile> [output-profile]" >&2
  exit 2
fi

test -s "$VMLINUX" || { echo "missing vmlinux: $VMLINUX" >&2; exit 1; }
test -s "$TEXT_PERF" || { echo "missing ETM text profile: $TEXT_PERF" >&2; exit 1; }

if ! command -v create_llvm_prof >/dev/null 2>&1; then
  echo "create_llvm_prof not found. Install google/autofdo v0.30.1 or newer." >&2
  exit 1
fi

create_llvm_prof \
  --binary="$VMLINUX" \
  --profile="$TEXT_PERF" \
  --profiler=text \
  --format=extbinary \
  --out="$OUT_PROFILE"

test -s "$OUT_PROFILE"
echo "autofdo_profile=$OUT_PROFILE"
sha256sum "$OUT_PROFILE"
