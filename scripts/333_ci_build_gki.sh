#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase333-gki-out"
FAIL="$PWD/phase333-gki-failure"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
SDEDBG="$ROOT/drivers/a52_display/msm/sde_dbg.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
STAGE=startup

stage() { STAGE="$1"; echo "== Phase333 stage: $STAGE =="; }
fail_report() {
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase333-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p333-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/333_apply_sde_debug_dump_internal_frontier.py scripts/333_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$SDEDBG" "$DISP"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase332"
bash scripts/332_ci_build_gki.sh 2>&1 | tee phase333-phase332.log
for f in phase332-gki-out/package/boot.img phase332-gki-out/compile/Image phase332-gki-out/config/final.config "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$SDEDBG" "$DISP"; do test -s "$f"; done
test "$(stat -c '%s' phase332-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$CTRL"
grep -Fq 'P276 332G q=2 dump=0' "$CTRL"
grep -Fq "fmt[7] == '1' || fmt[7] == '2'" "$REC"
grep -Fq 'static void _sde_dump_array(struct sde_dbg_reg_base *blk_arr[],' "$SDEDBG"

cp phase332-gki-out/config/final.config /tmp/p333-phase332.config
for spec in ctrl:$CTRL hwc:$HWC phy:$PHY phyv3:$PHYV3 rec:$REC sdedbg:$SDEDBG disp:$DISP; do
  n=${spec%%:*}; f=${spec#*:}; cp "$f" "/tmp/p333-${n}-before"
done

stage "apply SDE debug-dump internal frontier recorder"
python3 -m py_compile scripts/333_apply_sde_debug_dump_internal_frontier.py
python3 scripts/333_apply_sde_debug_dump_internal_frontier.py --root "$ROOT"
python3 scripts/333_apply_sde_debug_dump_internal_frontier.py --root "$ROOT" --check-only

cmp -s /tmp/p333-ctrl-before "$CTRL"
cmp -s /tmp/p333-hwc-before "$HWC"
cmp -s /tmp/p333-phy-before "$PHY"
cmp -s /tmp/p333-phyv3-before "$PHYV3"
cmp -s /tmp/p333-disp-before "$DISP"
! cmp -s /tmp/p333-rec-before "$REC"
! cmp -s /tmp/p333-sdedbg-before "$SDEDBG"
diff -u /tmp/p333-rec-before "$REC" > /tmp/p333-rec.diff || true
diff -u /tmp/p333-sdedbg-before "$SDEDBG" > /tmp/p333-sdedbg.diff || true
git -C "$ROOT" diff --check -- drivers/a52_display/msm/sde_dbg.c drivers/a52_secure/a52_ack_secure_flight_recorder.c

stage "strict Phase333 diagnostic-only scope audit"
python3 - "$SDEDBG" "$REC" <<'PY'
from pathlib import Path
import sys
bs=Path('/tmp/p333-sdedbg-before').read_text(); a=Path(sys.argv[1]).read_text()
br=Path('/tmp/p333-rec-before').read_text(); ar=Path(sys.argv[2]).read_text()
protected=(
 'readl_relaxed(', 'writel_relaxed(', 'readl(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
 'wmb(', 'mb(', 'rmb(', 'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
 'pm_runtime_get_sync(', 'pm_runtime_put_sync(', 'mutex_lock(', 'mutex_unlock(',
 'sde_evtlog_dump_all(', '_sde_dump_reg_all(', '_sde_dump_reg_by_ranges(',
 '_sde_dbg_dump_sde_dbg_bus(', '_sde_dbg_dump_vbif_dbg_bus(', 'dsi_ctrl_debug_dump(',
 'ss_store_xlog_panic_dbg(', 'panic(',
)
for t in protected:
    if bs.count(t) != a.count(t):
        raise SystemExit(f'Phase333 SDE functional-scope violation {t}: {bs.count(t)} -> {a.count(t)}')
if ar.count('atomic_set(&a52_r280_retained, 1);') != br.count('atomic_set(&a52_r280_retained, 1);'):
    raise SystemExit('Phase333 changed retention setter')
if ar.count('a52_r280_retained') != br.count('a52_r280_retained') + 1:
    raise SystemExit('Phase333 expected exactly one new retention-state read')
for t in (
 'P276 333A p=%u a=%u s=%u v=%u','P276 333B lock=1','P276 333C mem=%u sz=%u',
 'P276 333D evt=0','P276 333E evt=1','P276 333F reg=0','P276 333G reg=1',
 'P276 333H sde=0','P276 333I sde=1','P276 333J vbif=0','P276 333K vbif=1',
 'P276 333L dsi=0','P276 333M dsi=1','P276 333N xlog=0','P276 333O xlog=1',
 'P276 333P panic=%u pe=%u','P276 333Q panic=return','P276 333R unlock=0','P276 333S unlock=1'):
    if t not in a: raise SystemExit('Phase333 marker missing: '+t)
if "fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '3'" not in ar:
    raise SystemExit('Phase333 recorder namespace missing')
if 'EXPORT_SYMBOL_GPL(a52_ackfr_timeout_retained);' not in ar:
    raise SystemExit('Phase333 retained-state export missing')
print('Phase333 SDE debug-dump scope audit: PASS')
PY

stage "config invariant"
cp /tmp/p333-phase332.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase333-olddefconfig.log 2>&1
cmp -s /tmp/p333-phase332.config "$BUILD/.config"

stage "compile incremental Phase333 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase333-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for m in \
 'P276 333A p=%u a=%u s=%u v=%u' \
 'P276 333B lock=1' \
 'P276 333C mem=%u sz=%u' \
 'P276 333D evt=0' \
 'P276 333E evt=1' \
 'P276 333F reg=0' \
 'P276 333G reg=1' \
 'P276 333H sde=0' \
 'P276 333I sde=1' \
 'P276 333J vbif=0' \
 'P276 333K vbif=1' \
 'P276 333L dsi=0' \
 'P276 333M dsi=1' \
 'P276 333N xlog=0' \
 'P276 333O xlog=1' \
 'P276 333P panic=%u pe=%u' \
 'P276 333Q panic=return' \
 'P276 333R unlock=0' \
 'P276 333S unlock=1' \
 'P276 332G q=2 dump=0' \
 'P276 280Z q=2'; do grep -aFq "$m" "$IMAGE"; done

stage "package evidence and boot image"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase333-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/333_apply_sde_debug_dump_internal_frontier.py scripts/333_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p333-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$SDEDBG" "$CTRL" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase332-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase332-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE332-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase333-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn={
 'phase':'333','flavor':'gki','name':'SDE-DEBUG-DUMP-INTERNAL-FRONTIER-V1',
 'git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,'base_phase':'332',
 'phase332_hardware_finding':'Persistent exact-F0 timeout retention fired. The retained ring ended at P276 332G q=2 dump=0, immediately before synchronous SDE_DBG_DUMP(all,dbg_bus,vbif_dbg_bus,panic). No 332H return marker survived.',
 'experiment':'instrument inside _sde_dump_array after retention to bracket mutex, allocation, evtlog, register dump, SDE debug bus, VBIF debug bus, DSI debug bus, Samsung xlog panic store and the final panic decision.',
 'display_control_flow_changed':False,'sde_mmio_changed':False,'panic_behavior_changed':False,
 'clock_phy_reset_regulator_changed':False,
 'retention_behavior':'Phase280/332 retention remains; P276 333* is additionally admitted only after retention and emitted only while retained.',
 'image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage complete
echo 'Phase333 SDE debug-dump internal frontier recorder: PASS'
trap - EXIT
