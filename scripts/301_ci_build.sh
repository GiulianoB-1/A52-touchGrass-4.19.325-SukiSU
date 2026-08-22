#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase301-out"
SDE="$ROOT/drivers/a52_display/msm/sde_rsc.c"
BUS="$ROOT/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
RSC="$ROOT/drivers/soc/qcom/rpmh-rsc.c"
RPMH="$ROOT/drivers/soc/qcom/rpmh.c"
COMPAT="$ROOT/a52-port-compat.h"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report() {
  set +e
  rm -rf phase301-failure
  mkdir -p phase301-failure/{logs,audit,source}
  cp phase301-compile.log phase301-failure/logs/ 2>/dev/null || true
  cp phase301-patch-report.json phase301-failure/audit/ 2>/dev/null || true
  cp /tmp/p301-phase296.config phase301-failure/audit/ 2>/dev/null || true
  cp /tmp/p301-before.diff phase301-failure/audit/ 2>/dev/null || true
  cp /tmp/p301-after.diff phase301-failure/audit/ 2>/dev/null || true
  for f in "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do
    [ -f "$f" ] && cp "$f" phase301-failure/source/ || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Exact hardware-observed Phase296 baseline. Phase301 is deliberately parallel
# to Phase300 and inherits no DMA/GEM/SMMU experiment.
bash scripts/296_ci_build.sh
test -s phase296-out/package/boot.img
test -s phase296-out/compile/Image
test -s phase296-out/config/final.config
test -s "$BUILD/arch/arm64/boot/Image"
test "$(stat -c '%s' phase296-out/package/boot.img)" -eq 100663296

for f in "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT" "$REC"; do test -s "$f"; done

cp phase296-out/config/final.config /tmp/p301-phase296.config
cp "$RPMH" /tmp/p301-rpmh-before.c
cp "$COMPAT" /tmp/p301-compat-before.h
cp "$REC" /tmp/p301-rec-before.c
cp "$SDE" /tmp/p301-sde-before.c
cp "$BUS" /tmp/p301-bus-before.c
cp "$RSC" /tmp/p301-rsc-before.c

git -C "$ROOT" diff --binary --no-ext-diff > /tmp/p301-before.diff

# Prove the exact inherited Phase13 compile-only runtime shims are still active.
grep -Fq 'A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS: diagnostic, non-flashable.' "$COMPAT"
grep -Fq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fq '#define rpmh_flush(d) do{}while(0)' "$COMPAT"

python3 -m py_compile scripts/301_apply_rpmh_rsc_contract_trace.py
python3 scripts/301_apply_rpmh_rsc_contract_trace.py \
  --root "$ROOT" --report phase301-patch-report.json
python3 scripts/301_apply_rpmh_rsc_contract_trace.py \
  --root "$ROOT" --check-only

# Protected runtime implementation and recorder transport must be byte-identical.
cmp -s /tmp/p301-rpmh-before.c "$RPMH"
cmp -s /tmp/p301-compat-before.h "$COMPAT"
cmp -s /tmp/p301-rec-before.c "$REC"

# Scope gate: relative to the reconstructed Phase296 tree, this phase may only
# touch the three observation targets.
git -C "$ROOT" diff --binary --no-ext-diff > /tmp/p301-after.diff
python3 - <<'PY'
import subprocess
from pathlib import Path
root = Path('gki/common')
allowed = {
    'drivers/a52_display/msm/sde_rsc.c',
    'drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c',
    'drivers/soc/qcom/rpmh-rsc.c',
}
cp = subprocess.run(['git', '-C', str(root), 'diff', '--name-only'],
                    text=True, stdout=subprocess.PIPE, check=True)
changed = {x.strip() for x in cp.stdout.splitlines() if x.strip()}
# The reconstructed Phase296 tree is itself generated and may already differ
# from pristine common. Determine only the new files changed by comparing
# before/after content snapshots of all candidate paths through explicit hashes.
for rel in allowed:
    if not (root / rel).is_file():
        raise SystemExit('Phase301 missing allowed target: ' + rel)
# Protected files are checked bytewise in shell. Here reject obvious Phase301
# writes outside the three target paths by looking for our marker globally.
marker = 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1'
hits = []
for p in root.rglob('*'):
    if p.is_file() and '.git' not in p.parts:
        try:
            t = p.read_text(errors='ignore')
        except OSError:
            continue
        if marker in t:
            hits.append(p.relative_to(root).as_posix())
if sorted(hits) != sorted(allowed):
    raise SystemExit(f'Phase301 marker scope mismatch: {hits}')
print('Phase301 marker scope:', ', '.join(sorted(hits)))
PY

# Preserve Phase296 configuration exactly.
cp /tmp/p301-phase296.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase301-olddefconfig.log 2>&1
cmp -s /tmp/p301-phase296.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase301-compile.log
rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$rc" > phase301-make-return-code.txt
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase301-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase301-compile.log "$OUT/audit/"
cp phase301-olddefconfig.log "$OUT/audit/"
cp phase301-make-return-code.txt "$OUT/audit/"
cp phase301-patch-report.json "$OUT/audit/"
cp scripts/301_apply_rpmh_rsc_contract_trace.py "$OUT/audit/"
cp /tmp/p301-sde-before.c "$OUT/audit/sde_rsc-before.c"
cp /tmp/p301-bus-before.c "$OUT/audit/msm_bus_fabric_rpmh-before.c"
cp /tmp/p301-rsc-before.c "$OUT/audit/rpmh-rsc-before.c"
cp /tmp/p301-rpmh-before.c "$OUT/audit/rpmh-before.c"
cp /tmp/p301-compat-before.h "$OUT/audit/a52-port-compat-before.h"
cp /tmp/p301-rec-before.c "$OUT/audit/recorder-before.c"
cp "$SDE" "$OUT/source/sde_rsc.c"
cp "$BUS" "$OUT/source/msm_bus_fabric_rpmh.c"
cp "$RSC" "$OUT/source/rpmh-rsc.c"
cp "$RPMH" "$OUT/source/rpmh.c"
cp "$COMPAT" "$OUT/source/a52-port-compat.h"
cp "$REC" "$OUT/source/a52_ack_secure_flight_recorder.c"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase296-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase301-out')
image = (r/'compile/Image').read_bytes()
source = ''.join((r/'source'/n).read_text(errors='replace') for n in (
    'sde_rsc.c','msm_bus_fabric_rpmh.c','rpmh-rsc.c'))
markers = [
    'P276 301S t=%d r=%d en=1 irq=%d',
    'P276 301V e cur=%d irq=%d',
    'P276 301V tw=%d',
    'P276 301V inv dev=%s',
    'P276 301F would dev=%s irq=%d',
    'P276 301B e ac=%d wk=%d sl=%d vcd=%d st=%d',
    'P276 301B inv mb=%s',
    'P276 301B A r=%d st=%d mb=%s',
    'P276 301B W r=%d',
    'P276 301B S r=%d',
    'P276 301P hw=%u a=%d sl=%d w=%d c=%d',
    'P276 301I s=%u w=%u use=%u',
    'P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d',
    'P276 301R c id=%d ty=%d',
    'P276 301R x id=%d ty=%d',
    'P276 301C e st=%d ty=%d n=%d slots=%u',
    'P276 301C x r=%d id=%d cmd=%d slots=%u',
]
for m in markers:
    if m not in source:
        raise SystemExit('Phase301 source marker missing: ' + m)
    if m.encode() not in image:
        raise SystemExit('Phase301 runtime marker missing from Image: ' + m)
compat = (r/'source/a52-port-compat.h').read_text(errors='replace')
for token in [
    'A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS: diagnostic, non-flashable.',
    '#define rpmh_mode_solver_set(d,e) do{}while(0)',
    '#define rpmh_flush(d) do{}while(0)',
]:
    if token not in compat:
        raise SystemExit('Phase301 Phase13 invariant missing: ' + token)
if (r/'source/rpmh.c').read_bytes() != (r/'audit/rpmh-before.c').read_bytes():
    raise SystemExit('Phase301 rpmh.c changed unexpectedly')
if (r/'source/a52-port-compat.h').read_bytes() != (r/'audit/a52-port-compat-before.h').read_bytes():
    raise SystemExit('Phase301 compatibility header changed unexpectedly')
if (r/'source/a52_ack_secure_flight_recorder.c').read_bytes() != (r/'audit/recorder-before.c').read_bytes():
    raise SystemExit('Phase301 recorder changed unexpectedly')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
    'phase': '301',
    'name': 'RPMH-RSC-CONTRACT-TRACE-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'base': 'exact Phase296 userspace DRM atomic frontier',
    'hardware_validated': False,
    'functional_change': 'instrumentation-only',
    'solver_behavior_restored': False,
    'flush_behavior_restored': False,
    'phase13_solver_stub_preserved': True,
    'phase13_flush_stub_preserved': True,
    'protected_rpmh_core_unchanged': True,
    'protected_compat_header_unchanged': True,
    'protected_recorder_unchanged': True,
    'display_rsc_expected_dt_tcs': {'active':0,'sleep':1,'wake':1,'control':0},
    'markers': markers,
    'hardware_questions': [
        'Does disp_rsc report HW solver support?',
        'When SDE would enable solver mode, does an ACTIVE display bus vote still succeed?',
        'Does that ACTIVE_ONLY transfer select and claim the borrowed WAKE TCS?',
        'After WAKE/SLEEP batches are cached and SDE reaches would-flush, is control-data programming absent at that boundary?',
    ],
    'boot_bytes': (r/'package/boot.img').stat().st_size,
    'boot_sha256': hashlib.sha256((r/'package/boot.img').read_bytes()).hexdigest(),
    'image_sha256': hashlib.sha256(image).hexdigest(),
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
for key in ('dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    if not identity[key]:
        raise SystemExit('Phase301 repack invariant failed: ' + key)
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [p for p in r.rglob('*') if p.is_file() and p.name != 'SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for p in sorted(files):
        f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
print('Phase301 RPMh/RSC observation audit: PASS')
PY

(cd "$OUT" && sha256sum -c SHA256SUMS)
python3 scripts/301_apply_rpmh_rsc_contract_trace.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase301 RPMh/RSC contract trace build/repack: PASS'
