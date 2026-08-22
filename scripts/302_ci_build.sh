#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase302-out"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
SDE="$ROOT/drivers/a52_display/msm/sde_rsc.c"
BUS="$ROOT/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
RSC="$ROOT/drivers/soc/qcom/rpmh-rsc.c"
RPMH="$ROOT/drivers/soc/qcom/rpmh.c"
COMPAT="$ROOT/a52-port-compat.h"
EXPECTED_PHASE296_REC_SHA="0679e25f80d535bc16a05cd1ecaa4f9f2c78d0aa0d3f06f5b4813c7a4505156e"

fail_report() {
  set +e
  rm -rf phase302-failure
  mkdir -p phase302-failure/{logs,audit,source}
  cp phase302-compile.log phase302-failure/logs/ 2>/dev/null || true
  cp phase302-olddefconfig.log phase302-failure/logs/ 2>/dev/null || true
  cp phase302-patch-report.json phase302-failure/audit/ 2>/dev/null || true
  cp /tmp/p302-phase296.config phase302-failure/audit/ 2>/dev/null || true
  cp /tmp/p302-rec-before.c phase302-failure/audit/ 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" phase302-failure/source/ || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct and build the exact hardware-observed Phase296 baseline first.
bash scripts/296_ci_build.sh
test -s phase296-out/package/boot.img
test -s phase296-out/compile/Image
test -s phase296-out/config/final.config
test -s "$BUILD/arch/arm64/boot/Image"
test "$(stat -c '%s' phase296-out/package/boot.img)" -eq 100663296

for f in "$REC" "$SDE" "$BUS" "$RSC" "$RPMH" "$COMPAT"; do test -s "$f"; done

cp phase296-out/config/final.config /tmp/p302-phase296.config
cp "$REC" /tmp/p302-rec-before.c
cp "$SDE" /tmp/p302-sde-before.c
cp "$BUS" /tmp/p302-bus-before.c
cp "$RSC" /tmp/p302-rsc-before.c
cp "$RPMH" /tmp/p302-rpmh-before.c
cp "$COMPAT" /tmp/p302-compat-before.h

actual_rec_sha="$(sha256sum "$REC" | awk '{print $1}')"
if [ "$actual_rec_sha" != "$EXPECTED_PHASE296_REC_SHA" ]; then
  echo "Phase302 exact Phase296 recorder hash mismatch: $actual_rec_sha" >&2
  exit 1
fi

grep -Fq 'A52_PHASE226_ODSIGN_GATE_TRACE' "$REC"
grep -Fq 'ODSPOST 226 ts t=%u p=%d c=%.10s s=%lx x=%x r=%d o=%d i=%d' "$REC"

python3 -m py_compile scripts/302_apply_odsign_wait_channel_trace.py
python3 scripts/302_apply_odsign_wait_channel_trace.py \
  --root "$ROOT" --report phase302-patch-report.json
python3 scripts/302_apply_odsign_wait_channel_trace.py \
  --root "$ROOT" --check-only

# Scope gate: Phase302 may modify only the recorder. Display/RPMh and the
# compatibility semantics remain byte-identical to reconstructed Phase296.
cmp -s /tmp/p302-sde-before.c "$SDE"
cmp -s /tmp/p302-bus-before.c "$BUS"
cmp -s /tmp/p302-rsc-before.c "$RSC"
cmp -s /tmp/p302-rpmh-before.c "$RPMH"
cmp -s /tmp/p302-compat-before.h "$COMPAT"
python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
marker = 'A52_PHASE302_ODSIGN_WAIT_CHANNEL_TRACE_V1'
hits = []
for p in root.rglob('*'):
    if p.is_file() and '.git' not in p.parts:
        try:
            if marker in p.read_text(errors='ignore'):
                hits.append(p.relative_to(root).as_posix())
        except OSError:
            pass
expected = ['drivers/a52_secure/a52_ack_secure_flight_recorder.c']
if hits != expected:
    raise SystemExit(f'Phase302 marker scope mismatch: {hits}')
print('Phase302 marker scope:', hits[0])
PY

# Preserve Phase296 configuration exactly.
cp /tmp/p302-phase296.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase302-olddefconfig.log 2>&1
cmp -s /tmp/p302-phase296.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase302-compile.log
rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$rc" > phase302-make-return-code.txt
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase302-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
VMLINUX="$BUILD/vmlinux"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"
test -s "$VMLINUX"
test -s "$SYSTEM_MAP"
# Prove the pinned kernel actually provides the scheduler wait-channel API.
grep -Eq ' [Tt] get_wchan$' "$SYSTEM_MAP"

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$SYSTEM_MAP" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase302-compile.log "$OUT/audit/"
cp phase302-olddefconfig.log "$OUT/audit/"
cp phase302-make-return-code.txt "$OUT/audit/"
cp phase302-patch-report.json "$OUT/audit/"
cp scripts/302_apply_odsign_wait_channel_trace.py "$OUT/audit/"
cp /tmp/p302-rec-before.c "$OUT/audit/recorder-before.c"
cp "$REC" "$OUT/source/a52_ack_secure_flight_recorder.c"
cp "$SDE" "$OUT/source/sde_rsc.c"
cp "$BUS" "$OUT/source/msm_bus_fabric_rpmh.c"
cp "$RSC" "$OUT/source/rpmh-rsc.c"
cp "$RPMH" "$OUT/source/rpmh.c"
cp "$COMPAT" "$OUT/source/a52-port-compat.h"

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
r = Path('phase302-out')
image = (r/'compile/Image').read_bytes()
rec = (r/'source/a52_ack_secure_flight_recorder.c').read_text(errors='replace')
markers = [
    'P276 302W t=%u p=%d c=%.10s w=%ps',
    'P276 302A t=%u p=%d a=%lx',
]
for marker in markers:
    if marker not in rec:
        raise SystemExit('Phase302 source marker missing: ' + marker)
    if marker.encode() not in image:
        raise SystemExit('Phase302 Image marker missing: ' + marker)
if 'A52_PHASE226_ODSIGN_GATE_TRACE' not in rec:
    raise SystemExit('Phase302 inherited Phase226 snapshot marker missing')
if (r/'source/sde_rsc.c').read_bytes() != Path('/tmp/p302-sde-before.c').read_bytes():
    raise SystemExit('Phase302 changed SDE unexpectedly')
if (r/'source/msm_bus_fabric_rpmh.c').read_bytes() != Path('/tmp/p302-bus-before.c').read_bytes():
    raise SystemExit('Phase302 changed msm_bus unexpectedly')
if (r/'source/rpmh-rsc.c').read_bytes() != Path('/tmp/p302-rsc-before.c').read_bytes():
    raise SystemExit('Phase302 changed rpmh-rsc unexpectedly')
if (r/'source/rpmh.c').read_bytes() != Path('/tmp/p302-rpmh-before.c').read_bytes():
    raise SystemExit('Phase302 changed rpmh core unexpectedly')
if (r/'source/a52-port-compat.h').read_bytes() != Path('/tmp/p302-compat-before.h').read_bytes():
    raise SystemExit('Phase302 changed compatibility header unexpectedly')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
    'phase': '302',
    'name': 'ODSIGN-WAIT-CHANNEL-TRACE-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'base': 'exact Phase296 userspace DRM atomic frontier',
    'hardware_validated': False,
    'functional_change': 'instrumentation-only',
    'modified_runtime_source': 'drivers/a52_secure/a52_ack_secure_flight_recorder.c',
    'display_stack_unchanged': True,
    'rpmh_stack_unchanged': True,
    'compat_header_unchanged': True,
    'phase226_task_snapshot_preserved': True,
    'markers': markers,
    'hardware_questions': [
        'At 30/45/60/90 seconds, where are the persistent odsign and odrefresh tasks sleeping?',
        'Do repeated snapshots converge on one stable kernel wait channel?',
        'Does odrefresh ever leave that wait channel or exit before zygote/surfaceflinger admission?',
    ],
    'boot_bytes': (r/'package/boot.img').stat().st_size,
    'boot_sha256': hashlib.sha256((r/'package/boot.img').read_bytes()).hexdigest(),
    'image_sha256': hashlib.sha256(image).hexdigest(),
    'system_map_sha256': hashlib.sha256((r/'compile/System.map').read_bytes()).hexdigest(),
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
for key in ('dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    if not identity[key]:
        raise SystemExit('Phase302 repack invariant failed: ' + key)
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [p for p in r.rglob('*') if p.is_file() and p.name != 'SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for p in sorted(files):
        f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
print('Phase302 odsign/odrefresh wait-channel observation audit: PASS')
PY

(cd "$OUT" && sha256sum -c SHA256SUMS)
python3 scripts/302_apply_odsign_wait_channel_trace.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase302 odsign/odrefresh wait-channel trace build/repack: PASS'
