#!/usr/bin/env bash
# P446Z - pinned upstream ReSukiSU successor integration, built-in, tracepoint.
set -Eeuo pipefail
[[ $# -eq 2 ]] || { echo "usage: $0 GKI_SOURCE_ROOT KBUILD_OUT" >&2; exit 2; }
ROOT="$1"; BUILD="$2"
PIN=b2c595ddfa64b685e5a144ad37b7c95f912fb92a
EXPECT_DRIVER=35233
if [[ -e "$ROOT/KernelSU" ]]; then
  echo "P446Z KSU source already present: refusing stale or duplicate integration" >&2
  exit 1
fi
git clone --quiet --filter=blob:none --no-checkout https://github.com/Baka-SU/BakaSU.git "$ROOT/KernelSU"
git -C "$ROOT/KernelSU" checkout --quiet --detach "$PIN"
[[ "$(git -C "$ROOT/KernelSU" rev-parse HEAD)" == "$PIN" ]]
# Kbuild computes KSU_VERSION as 30000 + git rev-list --count HEAD + 700.
# Assert the number exported to the manager, not merely the checkout SHA.
KSU_REVISIONS="$(git -C "$ROOT/KernelSU" rev-list --count HEAD)"
KSU_DRIVER="$((30000 + KSU_REVISIONS + 700))"
if [[ "$KSU_DRIVER" != "$EXPECT_DRIVER" ]]; then
  echo "P446AC KSU DRIVER_MISMATCH expected=$EXPECT_DRIVER actual=$KSU_DRIVER commit=$PIN" >&2
  exit 1
fi
echo "P446AC KSU PIN_VERIFIED commit=$PIN driver=$KSU_DRIVER"
python3 -m py_compile scripts/446z_integrate_resukisu.py
python3 scripts/446z_integrate_resukisu.py --root "$ROOT"
python3 scripts/446z_integrate_resukisu.py --root "$ROOT" --check-only
"$ROOT/scripts/config" --file "$BUILD/.config" \
  -e KSU -e KSU_TRACEPOINT_HOOK -e KSU_MULTI_MANAGER_SUPPORT \
  -e KALLSYMS_ALL -e KPROBES \
  -d KSU_MANUAL_HOOK -d KSU_SUSFS -d KSU_DEBUG
echo "P446AC KSU pinned commit=$PIN driver=$KSU_DRIVER builtin=1 hooks=tracepoint setup=PASS"
