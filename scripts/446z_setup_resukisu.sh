#!/usr/bin/env bash
# P446Z - pinned upstream ReSukiSU successor integration, built-in, tracepoint.
set -Eeuo pipefail
[[ $# -eq 2 ]] || { echo "usage: $0 GKI_SOURCE_ROOT KBUILD_OUT" >&2; exit 2; }
ROOT="$1"; BUILD="$2"
TAG=v4.2.0-rc3
PIN=239e1e8871b8fcd51a6e5b3002e0ba522fdd99fb
if [[ -e "$ROOT/KernelSU" ]]; then
  echo "P446Z KSU source already present: refusing stale or duplicate integration" >&2
  exit 1
fi
git clone --quiet --filter=blob:none --no-checkout https://github.com/Baka-SU/BakaSU.git "$ROOT/KernelSU"
git -C "$ROOT/KernelSU" checkout --quiet --detach "$PIN"
[[ "$(git -C "$ROOT/KernelSU" rev-parse HEAD)" == "$PIN" ]]
[[ "$(git -C "$ROOT/KernelSU" rev-parse "refs/tags/$TAG^{commit}")" == "$PIN" ]]
python3 -m py_compile scripts/446z_integrate_resukisu.py
python3 scripts/446z_integrate_resukisu.py --root "$ROOT"
python3 scripts/446z_integrate_resukisu.py --root "$ROOT" --check-only
"$ROOT/scripts/config" --file "$BUILD/.config" \
  -e KSU -e KSU_TRACEPOINT_HOOK -e KSU_MULTI_MANAGER_SUPPORT \
  -e KALLSYMS_ALL -e KPROBES \
  -d KSU_MANUAL_HOOK -d KSU_SUSFS -d KSU_DEBUG
echo "P446AB KSU pinned tag=$TAG source=$PIN builtin=1 hooks=tracepoint setup=PASS"
