#!/usr/bin/env bash
set -Eeuo pipefail

SRC="$PWD/scripts/298_qemu_trace_lab.sh"
OUT="$PWD/workspace/phase298r-qemu-smmu-isolation.generated.sh"

test -s "$SRC"
mkdir -p "$PWD/workspace"

python3 - "$SRC" "$OUT" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
out = Path(sys.argv[2])
text = src.read_text()

needle = '''chmod +x "$TMP"
bash "$TMP"
'''
if text.count(needle) != 1:
    raise SystemExit('Phase298R: Phase298 execution tail missing or ambiguous')
if 'PHASE298R_QEMU_SMMU_ISOLATION_V1' in text:
    raise SystemExit('Phase298R: source Phase298 script already contains Phase298R isolation')

replacement = r'''# PHASE298R_QEMU_SMMU_ISOLATION_V1
# Phase298 successfully removed the explicit a52_* driver Kbuild grafts, but
# the reconstructed phone tree also carries ARM-SMMU work in the normal
# drivers/iommu path. Generic QEMU virtio DRM does not need the A52/Qualcomm
# SMMU path, so keep it out of this virtual-only build rather than changing
# phone-side IOMMU source merely to satisfy QEMU.
chmod +x "$TMP"
python3 - "$TMP" <<'PY298R'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
anchor = '''"$cfg" --file "$QOUT/.config" --set-val DEFAULT_HUNG_TASK_TIMEOUT 30
make -C "$ROOT" O="$QOUT" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig </dev/null
cp "$QOUT/.config" "$QART/qemu-final.config"
'''
if text.count(anchor) != 1:
    raise SystemExit('Phase298R: QEMU config finalization anchor missing or ambiguous')
block = '''"$cfg" --file "$QOUT/.config" --set-val DEFAULT_HUNG_TASK_TIMEOUT 30

# PHASE298R_QEMU_SMMU_CONFIG_ISOLATION_V1
# arm-smmu v2/v3 is irrelevant to the virtio-gpu/ftrace lab and the Phase298
# artifact proved ARM_SMMU=m was compiling a phone-modified arm-smmu.c against
# the 5.10 core bus API. Disable both implementations only in the QEMU config.
grep -nE '^CONFIG_(ARM_SMMU|ARM_SMMU_V3|ARM_SMMU_V3_PMU)=|^# CONFIG_(ARM_SMMU|ARM_SMMU_V3|ARM_SMMU_V3_PMU) is not set' \\
  "$QOUT/.config" > "$ANALYSIS/qemu-smmu-before-disable.txt" || true
for s in ARM_SMMU ARM_SMMU_V3 ARM_SMMU_V3_PMU; do
  "$cfg" --file "$QOUT/.config" -d "$s"
done
make -C "$ROOT" O="$QOUT" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig </dev/null

grep -nE '^CONFIG_(ARM_SMMU|ARM_SMMU_V3|ARM_SMMU_V3_PMU)=|^# CONFIG_(ARM_SMMU|ARM_SMMU_V3|ARM_SMMU_V3_PMU) is not set' \\
  "$QOUT/.config" > "$ANALYSIS/qemu-smmu-after-disable.txt" || true
if grep -Eq '^CONFIG_(ARM_SMMU|ARM_SMMU_V3|ARM_SMMU_V3_PMU)=' "$QOUT/.config"; then
  echo 'Phase298R: ARM SMMU unexpectedly remained enabled in generic QEMU config' >&2
  grep -nE '^CONFIG_(ARM_SMMU|ARM_SMMU_V3|ARM_SMMU_V3_PMU)=' "$QOUT/.config" >&2 || true
  exit 1
fi
printf '%s\\n' 'PHASE298R_QEMU_SMMU_ISOLATION_V1' > "$ANALYSIS/qemu-smmu-isolation.status"
cp "$QOUT/.config" "$QART/qemu-final.config"
'''
path.write_text(text.replace(anchor, block, 1))
print(f'Phase298R patched generated QEMU driver: {path}')
PY298R
bash "$TMP"
'''

out.write_text(text.replace(needle, replacement, 1))
print(f'Phase298R generated driver: {out}')
PY

chmod +x "$OUT"
bash "$OUT"
