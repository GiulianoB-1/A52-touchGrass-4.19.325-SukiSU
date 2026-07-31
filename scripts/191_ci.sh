#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-lagoon-fingerprint-gpio-reserve"
OUT="$PWD/artifacts/a52xq-display-component-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/190_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase191.config"
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" "$OUT/stage/msm-drv-before-phase191.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c" "$OUT/stage/dsi-display-before-phase191.c"
cp "$ROOT/drivers/base/component.c" "$OUT/stage/component-before-phase191.c"

python3 scripts/191_apply.py --root "$ROOT" | tee "$OUT/logs/phase191-apply.log"

cp "$ROOT/drivers/a52_display/msm/msm_drv.c" "$OUT/stage/msm-drv-after-phase191.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c" "$OUT/stage/dsi-display-after-phase191.c"
cp "$ROOT/drivers/base/component.c" "$OUT/stage/component-after-phase191.c"
cp scripts/191_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase191.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
msm = (root / 'drivers/a52_display/msm/msm_drv.c').read_text()
dsi = (root / 'drivers/a52_display/msm/dsi/dsi_display.c').read_text()
comp = (root / 'drivers/base/component.c').read_text()
lagoon = (root / 'drivers/pinctrl/qcom/pinctrl-lagoon.c').read_text()
gpio = (root / 'drivers/gpio/gpiolib.c').read_text()
dd = (root / 'drivers/base/dd.c').read_text()
audit = (root / 'drivers/a52_secure/a52_display_bind_audit.c').read_text()
for marker in (
    'DRMCOMP collect enter',
    'DRMCOMP connectors prop=%u len=%d',
    'DRMCOMP connector i=%u',
    'DRMCOMP match-add i=%u',
    'DRMCOMP master-add exit rc=%d',
):
    assert marker in msm, marker
for marker in (
    'DRMCOMP component-add enter',
    'DRMCOMP component-add exit',
):
    assert marker in dsi, marker
for marker in (
    'COMP master-add enter',
    'COMP master stage=%s',
    'COMP slot i=%zu',
    'COMP component-add enter',
    'COMP component-add result',
):
    assert marker in comp, marker
reserved = lagoon.split('static const int lagoon_reserved_gpios[] = {', 1)[1].split('};', 1)[0]
assert '13, 14, 15, 16,' in reserved
assert 'CONFIG_FINGERPRINT_SECURE' not in reserved
assert 'GPIOCORE dir-read enter pin=%u' in gpio
assert 'direction = gc->get_direction(gc, i);' in gpio
assert 'DISP RP bypass' not in dd
assert dd.count('a52_device_links_force_probe(dev, &kept, &dropped);') == 1
assert 'retry_all(' not in audit
assert 'device_attach(' not in audit
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase191-display-component-trace.patch"
test -s "$OUT/stage/phase191-display-component-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase191-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase191-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase191-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase191-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in \
  'drivers/base/component.o' \
  'drivers/a52_display/msm/msm_drv.o' \
  'drivers/a52_display/msm/dsi/dsi_display.o'; do
  grep -Fq "$object" "$OUT/logs/phase191-compile.log"
done

for marker in \
  'DRMCOMP connectors prop=%u len=%d' \
  'DRMCOMP master-add exit rc=%d' \
  'DRMCOMP component-add exit dev=%s rc=%d' \
  'COMP master-add enter dev=%s num=%zu' \
  'COMP slot i=%zu found=%u dev=%s bound=%u dup=%u' \
  'PINCTRL Lagoon reserved secure=13-16'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done
if grep -aFq 'DISP RP bypass' "$BUILD/arch/arm64/boot/Image"; then
  echo 'forbidden display supplier-bypass marker remains in Image'
  exit 1
fi

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
A52 GKI 5.10 phase 191 DRM component assembly trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 190 hardware result:
  - GPIOs 13-16 were skipped successfully
  - GPIO/TLMM registration completed
  - BOOT_READY was reached
  - the kernel stayed alive for at least 61.468 seconds
  - Android userspace activity was present
  - the screen changed from an artifacted Samsung logo to black
  - no DRM master bind, DSI component bind, KMS init, panel init, or commit scope appeared

Phase 191 is instrumentation-only. It records:
  - SDE connector-property presence and length
  - every connector phandle and component-match addition
  - DRM master registration result
  - DSI component registration result
  - component-framework match slots, found devices, and bound state

It does not change component matching or return values. It does not change panel
commands, reset timing, backlight, clocks, regulators, display modes, DTB,
ramdisk, or recovery DTBO. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-display-component-trace')
base = json.loads(Path('artifacts/a52xq-lagoon-fingerprint-gpio-reserve/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
msm = (root / 'stage/msm-drv-after-phase191.c').read_text()
dsi = (root / 'stage/dsi-display-after-phase191.c').read_text()
comp = (root / 'stage/component-after-phase191.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-display-component-trace-audited',
    'phase': 191,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase190': False,
    'phase190_gpio_fix_hardware_validated': True,
    'phase190_kernel_alive_ms': 61468.096,
    'phase190_display_state': 'platform-probed-component-master-not-bound',
    'drm_component_collection_trace_added':
        'DRMCOMP connectors prop=%u len=%d' in msm,
    'dsi_component_add_trace_added':
        'DRMCOMP component-add exit dev=%s rc=%d' in dsi,
    'component_core_match_trace_added':
        'COMP slot i=%zu found=%u dev=%s bound=%u dup=%u' in comp,
    'component_control_flow_changed': False,
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
for key in (
    'phase190_gpio_fix_hardware_validated',
    'drm_component_collection_trace_added',
    'dsi_component_add_trace_added',
    'component_core_match_trace_added',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(
    json.dumps(audit, indent=2, sort_keys=True) + '\n'
)
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
