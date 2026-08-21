#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase293-failure
  mkdir -p phase293-failure/{source,logs,audit}
  cp phase293-compile.log phase293-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$HW" "$SMMU" "$REC"; do
    [ -f "$f" ] && cp "$f" phase293-failure/source/ || true
  done
  cp scripts/293_apply_gki_dma_done_reference.py phase293-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Phase293 deliberately reconstructs the clean Phase280 lineage. It does not
# call Phase281..292 and therefore cannot inherit the Phase282 FIFO reroute or
# Phase291 zero-rate recovery. Phase280 also supplies the proven timeout
# retention latch needed to preserve this failing early display transaction.
bash scripts/280_ci_build.sh
test -s phase280-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$HW" "$SMMU" "$REC"; do test -s "$f"; done

# The repack chain must remain the established fixed-size A52 boot container.
test "$(stat -c '%s' phase280-out/package/boot.img)" -eq 100663296

cp "$OUT/.config" /tmp/p293-base.config
cp "$DSI" /tmp/p293-dsi-before.c
cp "$HW" /tmp/p293-hw-before.c
cp "$SMMU" /tmp/p293-smmu-before.c
cp "$REC" /tmp/p293-rec-before.c

# Refuse accidental inheritance of the experiments this comparison is meant
# to remove. The stock secure-session FETCH_MEMORY -> FIFO safeguard is
# validated structurally by the Phase293 patcher immediately below.
for marker in \
  'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1' \
  'A52_PHASE292_DSI_CHAIN_TAPS_V1' \
  'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1'; do
  if grep -Fq "$marker" "$DSI"; then
    echo "Phase293 refuses later behavioral lineage: $marker" >&2
    exit 1
  fi
done

grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$DSI"
grep -Fq 'P276 280Z q=2' "$DSI"

python3 -m py_compile scripts/293_apply_gki_dma_done_reference.py
python3 scripts/293_apply_gki_dma_done_reference.py --root "$ROOT"
python3 scripts/293_apply_gki_dma_done_reference.py --root "$ROOT" --check-only

# Phase293 may touch only the DSI controller and common HW source. SMMU and the
# Phase280 retention backend must remain byte-for-byte unchanged.
cmp -s /tmp/p293-smmu-before.c "$SMMU"
cmp -s /tmp/p293-rec-before.c "$REC"

# Preserve the exact Phase280 kernel configuration.
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p293-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase293-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase293-out
mkdir -p phase293-out/{compile,config,package,audit,source}
cp "$IMAGE" phase293-out/compile/Image
cp "$OUT/.config" phase293-out/config/final.config
cp /tmp/p293-base.config phase293-out/audit/phase280-final.config
cp /tmp/p293-dsi-before.c phase293-out/audit/dsi-ctrl-before.c
cp /tmp/p293-hw-before.c phase293-out/audit/dsi-ctrl-hw-before.c
cp /tmp/p293-smmu-before.c phase293-out/audit/arm-smmu-before.c
cp /tmp/p293-rec-before.c phase293-out/audit/recorder-before.c
cp phase293-compile.log phase293-out/audit/
cp scripts/293_apply_gki_dma_done_reference.py phase293-out/audit/
cp "$DSI" phase293-out/source/dsi_ctrl.c
cp "$HW" phase293-out/source/dsi_ctrl_hw_cmn.c
cp "$SMMU" phase293-out/source/arm-smmu.c
cp "$REC" phase293-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase293-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase280-out/package/boot.img \
  --kernel phase293-out/package/Image.gz \
  --output phase293-out/package/boot.img \
  --report phase293-out/package/repack-report.json

test "$(stat -c '%s' phase293-out/package/boot.img)" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase293-out')
idn={
 'phase':'293',
 'name':'GKI-FETCH-MEMORY-DMA-DONE-REFERENCE',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase280 retained-timeout GKI reconstruction',
 'target':'ctrl0 / incoming DSI_CTRL_CMD_FETCH_MEMORY(0x20) / msg.flags=0x8 / type=0x29 / tx_len=3 / payload expected F05A5A',
 'transport_change':False,
 'fifo_reroute':False,
 'clock_recovery':False,
 'clk_set_rate_added':False,
 'dsi_register_writes_added':False,
 'wait_or_timeout_change':False,
 'panel_or_brightness_change':False,
 'retention':'inherited Phase280 timeout snapshot latch; GDM records emitted before P276 280Z',
 'gdm_schema':{
   'S00':'target identity and first three payload bytes',
   'S01':'selected controller/hw flags plus panel/controller engine state',
   'S02':'cached target byte/pixel/byte-intf/esc rates; CCF leaf objects are not owned by this GKI dsi_ctrl path',
   'S03':'pre DMA_DONE arm raw INT/STATUS/LANE/CLK',
   'S04':'post arm/reinit raw INT/STATUS/LANE/CLK',
   'S05':'pre production SW-trigger DMA_CTRL/OFFSET/LENGTH/FIFO/TRIG/SW/CLK',
   'S06':'immediately post production SW-trigger STATUS/FIFO/LANE/CLK/TPG/INT',
   'S07':'DMA_DONE ISR translated status/raw INT/error/irq-before, if observed',
   'S08':'normal completion wait return/irq/raw INT/STATUS',
   'S09':'timeout-final STATUS/FIFO/LANE/CLK/ACK/TIMEOUT/PHY/CTRL before retention latch'
 },
 'hardware_question':'On the same unmodified 0x20 memory-fetch transport that succeeds on Golden, what is the first GKI register/IRQ state that diverges before DMA_DONE fails to assert?'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[
 'compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json',
 'audit/phase280-final.config','audit/dsi-ctrl-before.c','audit/dsi-ctrl-hw-before.c',
 'audit/arm-smmu-before.c','audit/recorder-before.c','audit/phase293-compile.log',
 'audit/293_apply_gki_dma_done_reference.py','source/dsi_ctrl.c','source/dsi_ctrl_hw_cmn.c',
 'source/arm-smmu.c','source/a52_ack_secure_flight_recorder.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files:
  f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase293-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase293-out')
d=(r/'source/dsi_ctrl.c').read_text(); h=(r/'source/dsi_ctrl_hw_cmn.c').read_text(); img=(r/'compile/Image').read_bytes()
for bad in ['A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1','A52_PHASE292_DSI_CHAIN_TAPS_V1','A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1']:
 if bad in d: raise SystemExit('Phase293 forbidden inherited behavior: '+bad)
for m in ['A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1','GDM S00 c=0 in=%x mf=%x t=%x l=%u','GDM S01 sel=%x hw=%x pm=%u pwr=%u','GDM S02 ct=%u,%u,%u,%u ca=na','GDM S03 irq=%d in=%x st=%x','GDM S04 irq=%d in=%x st=%x','GDM S07 seen=1 st=%x in=%x irq0=%d','GDM S08 ret=%d irq=%d in=%x st=%x','GDM S09 st=%x fs=%x ln=%x ck=%x','GDM DONE success=0 target=0/8/20/29/3','P276 280Z q=2']:
 if m not in d: raise SystemExit('Phase293 source marker missing: '+m)
for m in ['A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1','GDM S05 dc=%x off=%x len=%x fc=%x','GDM S06 st=%x fs=%x ln=%x ck=%x']:
 if m not in h: raise SystemExit('Phase293 HW source marker missing: '+m)
for m in ['GDM S00 c=0 in=%x mf=%x t=%x l=%u','GDM S00p p=%02x%02x%02x','GDM S05 dc=%x off=%x len=%x fc=%x','GDM S06 st=%x fs=%x ln=%x ck=%x','GDM S08 ret=%d irq=%d in=%x st=%x','GDM S09 st=%x fs=%x ln=%x ck=%x','GDM DONE success=0 target=0/8/20/29/3','P276 280Z q=2']:
 if m.encode() not in img: raise SystemExit('Phase293 runtime marker missing from Image: '+m)
print('Phase293 compiled passive GDM audit: PASS')
PY

python3 scripts/293_apply_gki_dma_done_reference.py --root "$ROOT" --check-only

trap - EXIT
echo 'Phase293 clean-Phase280 GKI memory-DMA reference build/repack: PASS'
