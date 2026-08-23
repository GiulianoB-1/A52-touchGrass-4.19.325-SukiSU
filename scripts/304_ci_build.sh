#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase304-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
SDE="$ROOT/drivers/a52_display/msm/sde_rsc.c"
BUS="$ROOT/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
RSC="$ROOT/drivers/soc/qcom/rpmh-rsc.c"
RPMH="$ROOT/drivers/soc/qcom/rpmh.c"
COMPAT="$ROOT/a52-port-compat.h"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report() {
  set +e
  rm -rf phase304-failure
  mkdir -p phase304-failure/{logs,audit,source}
  cp phase304-compile.log phase304-olddefconfig.log phase304-failure/logs/ 2>/dev/null || true
  for f in "$CTRL" "$HW" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do
    [ -f "$f" ] && cp "$f" phase304-failure/source/ || true
  done
  cp /tmp/p304-* phase304-failure/audit/ 2>/dev/null || true
  cp scripts/304_apply_exact_f05a5a_visibility.py phase304-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Build the already-audited Phase301 observer first. Phase301 itself reconstructs
# the exact Phase296 physical-phone lineage and changes only SDE RSC, display
# msm_bus and disp_rsc observation sites. Phase304 then adds only the exact
# Phase303 F0 5A 5A persistent-visibility formatting to the inherited DSI trace.
bash scripts/301_ci_build.sh

test -s phase301-out/package/boot.img
test -s phase301-out/compile/Image
test -s phase301-out/config/final.config
test "$(stat -c '%s' phase301-out/package/boot.img)" -eq 100663296
for f in "$CTRL" "$HW" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do test -s "$f"; done

# Confirm Phase301 is really present and the Phase13 behavior erasures remain.
grep -Fq 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' "$SDE"
grep -Fq 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' "$BUS"
grep -Fq 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' "$RSC"
grep -Fq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fq '#define rpmh_flush(d) do{}while(0)' "$COMPAT"

# Confirm the clean inherited DSI reference and retention path are present.
grep -Fq 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1' "$CTRL"
grep -Fq 'A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1' "$HW"
grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$CTRL"
grep -Fq 'P276 280Z q=2' "$CTRL"

cp phase301-out/config/final.config /tmp/p304-phase301.config
cp "$CTRL" /tmp/p304-ctrl-before.c
cp "$HW" /tmp/p304-hw-before.c
cp "$SDE" /tmp/p304-sde-before.c
cp "$BUS" /tmp/p304-bus-before.c
cp "$RSC" /tmp/p304-rsc-before.c
cp "$RPMH" /tmp/p304-rpmh-before.c
cp "$COMPAT" /tmp/p304-compat-before.h
cp "$REC" /tmp/p304-rec-before.c

python3 -m py_compile scripts/304_apply_exact_f05a5a_visibility.py
python3 scripts/304_apply_exact_f05a5a_visibility.py --root "$ROOT"
python3 scripts/304_apply_exact_f05a5a_visibility.py --root "$ROOT" --check-only

# Phase304 must not touch the Phase301 observer, RPMh core, compatibility ABI or
# recorder transport. Only the inherited Phase293 DSI recorder formatting/scope
# is changed in this second step.
cmp -s /tmp/p304-sde-before.c "$SDE"
cmp -s /tmp/p304-bus-before.c "$BUS"
cmp -s /tmp/p304-rsc-before.c "$RSC"
cmp -s /tmp/p304-rpmh-before.c "$RPMH"
cmp -s /tmp/p304-compat-before.h "$COMPAT"
cmp -s /tmp/p304-rec-before.c "$REC"
! cmp -s /tmp/p304-ctrl-before.c "$CTRL"
! cmp -s /tmp/p304-hw-before.c "$HW"

# Prove the DSI visibility patch adds no behavior-changing primitive.
python3 - <<'PY'
from pathlib import Path
pairs = [
    (Path('/tmp/p304-ctrl-before.c'), Path('gki/common/drivers/a52_display/msm/dsi/dsi_ctrl.c')),
    (Path('/tmp/p304-hw-before.c'), Path('gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')),
]
tokens = [
    'DSI_W32(', 'writel(', 'writel_relaxed(', 'clk_set_rate(',
    'wait_for_completion_timeout(', 'msleep(', 'usleep_range(',
    'DSI_CTRL_CMD_FIFO_STORE', 'rpmh_mode_solver_set(', 'rpmh_flush(',
]
for before, after in pairs:
    b = before.read_text()
    a = after.read_text()
    for token in tokens:
        if b.count(token) != a.count(token):
            raise SystemExit(
                f'Phase304 behavior primitive count changed: {after} {token} '
                f'{b.count(token)}->{a.count(token)}')
print('Phase304 DSI behavior primitive audit: PASS')
PY

# Preserve Phase301/Phase296 configuration byte-for-byte.
cp /tmp/p304-phase301.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase304-olddefconfig.log 2>&1
cmp -s /tmp/p304-phase301.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase304-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase304-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

# Require both observer families in the final kernel image.
for marker in \
  'P276 301S t=%d r=%d en=1 irq=%d' \
  'P276 301V e cur=%d irq=%d' \
  'P276 301F would dev=%s irq=%d' \
  'P276 301B A r=%d st=%d mb=%s' \
  'P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d' \
  'P276 301R x id=%d ty=%d' \
  'P276 301I s=%u w=%u use=%u' \
  'P276 303 S00 c=0 in=%x mf=%x t=%x l=%u' \
  'P276 303 S00p p=%02x%02x%02x' \
  'P276 303 S03 irq=%d in=%x st=%x' \
  'P276 303 S04 irq=%d in=%x st=%x' \
  'P276 303 S05 dc=%x off=%x len=%x fc=%x' \
  'P276 303 S06 st=%x fs=%x ln=%x ck=%x' \
  'P276 303 S07 seen=1 st=%x in=%x irq0=%d' \
  'P276 303 S08 ret=%d irq=%d in=%x st=%x' \
  'P276 303 S09 st=%x fs=%x ln=%x ck=%x' \
  'P276 303 DONE success=0 target=0/8/20/29/3' \
  'P276 280Z q=2'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase304-compile.log phase304-olddefconfig.log "$OUT/audit/"
cp scripts/301_apply_rpmh_rsc_contract_trace.py "$OUT/audit/"
cp scripts/304_apply_exact_f05a5a_visibility.py "$OUT/audit/"
for f in /tmp/p304-*; do [ -f "$f" ] && cp "$f" "$OUT/audit/"; done
cp "$CTRL" "$OUT/source/dsi_ctrl.c"
cp "$HW" "$OUT/source/dsi_ctrl_hw_cmn.c"
cp "$SDE" "$OUT/source/sde_rsc.c"
cp "$BUS" "$OUT/source/msm_bus_fabric_rpmh.c"
cp "$RSC" "$OUT/source/rpmh-rsc.c"
cp "$RPMH" "$OUT/source/rpmh.c"
cp "$COMPAT" "$OUT/source/a52-port-compat.h"
cp "$REC" "$OUT/source/a52_ack_secure_flight_recorder.c"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase301-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase304-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
    'phase': '304',
    'name': 'RPMH-RSC-F05A5A-CORRELATION-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'base': 'exact Phase296 reconstruction plus Phase301 observation-only RPMh/RSC trace',
    'hardware_validated': False,
    'functional_change': 'instrumentation-only',
    'phase13_solver_stub_preserved': True,
    'phase13_flush_stub_preserved': True,
    'solver_behavior_restored': False,
    'flush_behavior_restored': False,
    'dsi_control_flow_change': False,
    'wait_or_timeout_change': False,
    'fifo_reroute': False,
    'clock_recovery': False,
    'recorder_transport_change': False,
    'phase303_hardware_capture': 'A52_RAW_RAMOOPS_20260823_123924.zip',
    'phase303_hardware_result': [
        'exact F0 5A 5A normal FETCH_MEMORY transaction reached',
        'Golden-compatible IRQ arm state observed before kickoff',
        'after trigger STATUS became 3 but raw DMA_DONE remained zero',
        'no Phase303 S07 DMA_DONE ISR record was emitted',
        'completion timed out after about 200 ms with irq=0',
        'IOVA translation/root and SMMU global fault state remained stable through timeout',
    ],
    'hardware_question': (
        'During the same real F0 5A 5A transaction that fails to assert DMA_DONE, '
        'does the Phase13-erased display RPMh solver/flush contract produce an '
        'observable ACTIVE-vote / borrowed-WAKE-TCS / missing-flush runtime divergence?'
    ),
    'boot_bytes': (r/'package/boot.img').stat().st_size,
    'boot_sha256': hashlib.sha256((r/'package/boot.img').read_bytes()).hexdigest(),
    'image_sha256': hashlib.sha256((r/'compile/Image').read_bytes()).hexdigest(),
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
for key in ('dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    if not identity[key]:
        raise SystemExit('Phase304 repack invariant failed: ' + key)
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [p for p in r.rglob('*') if p.is_file() and p.name != 'SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for p in sorted(files):
        f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
print('Phase304 combined observation audit: PASS')
PY

(cd "$OUT" && sha256sum -c SHA256SUMS)
python3 scripts/301_apply_rpmh_rsc_contract_trace.py --root "$ROOT" --check-only
python3 scripts/304_apply_exact_f05a5a_visibility.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase304 RPMh/RSC + exact F0 5A 5A correlation build/repack: PASS'
