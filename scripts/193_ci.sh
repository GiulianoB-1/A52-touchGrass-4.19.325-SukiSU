#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-sde-rscc-probe-trace"
OUT="$PWD/artifacts/a52xq-rscc-drivercore-gate-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/192_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase193.config"
cp "$ROOT/drivers/base/core.c" "$OUT/stage/core-before-phase193.c"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-before-phase193.c"

python3 scripts/193_apply.py --root "$ROOT" | tee "$OUT/logs/phase193-apply.log"

cp "$ROOT/drivers/base/core.c" "$OUT/stage/core-after-phase193.c"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-after-phase193.c"
cp scripts/193_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase193.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
core = (root / 'drivers/base/core.c').read_text()
dd = (root / 'drivers/base/dd.c').read_text()
rsc = (root / 'drivers/a52_display/msm/sde_rsc.c').read_text()
for marker in (
    'RSCCCORE links begin tag=%s c=%s status=%u',
    'RSCCCORE link n=%u s=%s st=%u fl=0x%x',
    'RSCCCORE link n=%u of=%s drv=%s',
    'RSCCCORE links end tag=%s count=%u',
):
    assert marker in core, marker
for marker in (
    'RSCCCORE match path=device-attach',
    'RSCCCORE match path=driver-attach',
    'RSCCCORE driver-probe enter',
    'RSCCCORE really-probe enter',
    'RSCCCORE suppliers dev=%s rc=%d reason=%s',
    'RSCCCORE pinctrl dev=%s rc=%d',
    'RSCCCORE dma dev=%s rc=%d',
    'RSCCCORE sysfs dev=%s rc=%d',
    'RSCCCORE busprobe enter',
    'RSCCCORE busprobe exit',
    'RSCCCORE really-probe done',
):
    assert marker in dd, marker
assert dd.count('ret = device_links_check_suppliers(dev);') == 1
assert dd.count('ret = driver_match_device(drv, dev);') >= 2
assert 'device_link_drop_managed(link);' in core
# Phase 193 must not call the mutating helper for RSCC.
rscc_block = dd.split('static bool a52_rscc_probe_device', 1)[1]
assert 'a52_device_links_force_probe(dev' not in rscc_block.split('static bool a52_legacy_fw_devlink_consumer', 1)[0]
for marker in (
    'RSCC main-register enter',
    'RSCC probe enter dev=%s node=%s counter=%d rpmh=%u',
    'RSCC component-add exit rc=%d',
):
    assert marker in rsc, marker
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase193-rscc-drivercore-gate-trace.patch"
test -s "$OUT/stage/phase193-rscc-drivercore-gate-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase193-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase193-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase193-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase193-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in 'drivers/base/core.o' 'drivers/base/dd.o'; do
  grep -Fq "$object" "$OUT/logs/phase193-compile.log"
done
for marker in \
  'RSCCCORE match path=driver-attach dev=%s drv=%s rc=%d' \
  'RSCCCORE really-probe enter dev=%s drv=%s' \
  'RSCCCORE suppliers dev=%s rc=%d reason=%s' \
  'RSCCCORE link n=%u s=%s st=%u fl=0x%x' \
  'RSCCCORE busprobe exit dev=%s rc=%d' \
  'RSCC main-register enter' \
  'DRMCOMP connectors prop=%u len=%d' \
  'PINCTRL Lagoon reserved secure=13-16'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source source/extracted/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r179-rs-recorder.py" --self-test
python3 "$OUT/tools/decode-a52-r180-soft-rs.py" --self-test
python3 "$OUT/tools/decode-a52-r188-near-header.py" --self-test

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 193 RSCC driver-core gate trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 192 hardware result:
  - RSCC-RPMh driver registered and its child probe returned 0
  - qcom,sde-rsc DT node and platform device exist
  - the main sde_rsc driver registered with rc=0
  - the main sde_rsc probe callback was never entered
  - BOOT_READY was reached and the kernel stayed alive through 58.396 seconds

Phase 193 is instrumentation-only. It records:
  - platform/device-driver match return values for qcom,sde-rsc
  - driver_probe_device and really_probe entry/exit
  - every supplier device link, status, flags, supplier driver and OF node
  - supplier-check return and deferred reason
  - generic pinctrl, DMA, sysfs, PM and bus-probe gates

The device-link trace is read-only. It does not drop, promote, bypass or create links.
It does not force probe, alter an errno, remove RSCC from the DRM match list,
or change display commands, timing, clocks, regulators, DTB, ramdisk or
recovery DTBO. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-rscc-drivercore-gate-trace')
base = json.loads(Path('artifacts/a52xq-sde-rscc-probe-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
core = (root / 'stage/core-after-phase193.c').read_text()
dd = (root / 'stage/dd-after-phase193.c').read_text()
image = root / 'compile/Image'; boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-rscc-drivercore-gate-trace-audited',
    'phase': 193,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase192': False,
    'phase192_hardware_validated': True,
    'phase192_kernel_alive_ms': 58396.048,
    'phase192_result': 'main-rscc-driver-registered-probe-callback-not-entered',
    'rscc_match_trace_added': 'RSCCCORE match path=driver-attach' in dd,
    'rscc_supplier_trace_added': 'RSCCCORE suppliers dev=%s rc=%d reason=%s' in dd,
    'rscc_link_enumeration_added': 'RSCCCORE link n=%u s=%s st=%u fl=0x%x' in core,
    'device_link_state_changed': False,
    'probe_control_flow_changed': False,
    'probe_return_values_changed': False,
    'dtb_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'display_modes_changed': False,
    'regulator_voltage_changed': False,
    'storage_write_added': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in ('phase192_hardware_validated','rscc_match_trace_added',
            'rscc_supplier_trace_added','rscc_link_enumeration_added',
            'dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True)+'\n')
PY
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
