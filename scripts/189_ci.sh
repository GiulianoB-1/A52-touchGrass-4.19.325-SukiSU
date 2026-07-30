#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-pinctrl-stage-trace"
OUT="$PWD/artifacts/a52xq-gpiolib-stage-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/188_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase189.config"
cp "$ROOT/drivers/gpio/gpiolib.c" "$OUT/stage/gpiolib-before-phase189.c"

python3 scripts/189_apply.py --root "$ROOT" | tee "$OUT/logs/phase189-apply.log"

cp "$ROOT/drivers/gpio/gpiolib.c" "$OUT/stage/gpiolib-after-phase189.c"
cp scripts/189_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase189.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
gpio = (root / 'drivers/gpio/gpiolib.c').read_text()
dd = (root / 'drivers/base/dd.c').read_text()
audit = (root / 'drivers/a52_secure/a52_display_bind_audit.c').read_text()
markers = (
    'GPIOCORE add enter',
    'GPIOCORE of-add enter',
    'GPIOCORE valid-init enter',
    'GPIOCORE dir-scan enter',
    'GPIOCORE dir-read enter pin=%u',
    'GPIOCORE dir-read exit pin=%u rc=%d',
    'GPIOCORE irq-add enter',
    'GPIOCORE setup-dev enter',
    'GPIOCORE add success',
)
for marker in markers:
    assert marker in gpio, marker
assert 'direction = gc->get_direction(gc, i);' in gpio
assert 'trace_android_vh_gpio_block_read' in gpio
assert 'DISP RP bypass' not in dd
assert dd.count('a52_device_links_force_probe(dev, &kept, &dropped);') == 1
assert 'retry_all(' not in audit
assert 'device_attach(' not in audit
PY

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase189-gpiolib-stage-trace.patch"
test -s "$OUT/stage/phase189-gpiolib-stage-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase189-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase189-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase189-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase189-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/gpio/gpiolib.o' "$OUT/logs/phase189-compile.log"

for marker in \
  'PINCTRL gpio chip-add enter' \
  'GPIOCORE add enter' \
  'GPIOCORE of-add enter' \
  'GPIOCORE valid-init enter' \
  'GPIOCORE dir-scan enter' \
  'GPIOCORE dir-read enter pin=%u' \
  'GPIOCORE irq-add enter' \
  'GPIOCORE add success'; do
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
A52 GKI 5.10 phase 189 Lagoon GPIO-core stage-trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase-188 hardware evidence:
  - PDC and AMOLED provider initialization completed
  - TLMM allocation, MMIO mapping, parent IRQ lookup and pinctrl registration completed
  - wakeup-parent domain lookup and GPIO parent allocation completed
  - the final persistent record was GPIO chip registration entry

Phase 189 is instrumentation-only. It traces the generic 5.10 GPIO-core
registration path, including OF registration, valid-mask setup, every eager GPIO
direction read, pin ranges, IRQ-chip setup and GPIO device setup.

TouchGrass does not eagerly read every GPIO direction during chip registration,
while common 5.10 does. This candidate does not change that behavior yet. It
identifies whether the reset occurs before the scan, at a specific GPIO register
read, or later in IRQ/device registration.

No DTB, panel command, timing, refresh mode, regulator voltage, supplier-link
handling, ramdisk or recovery DTBO is changed. Compile-audited, not hardware
validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-gpiolib-stage-trace')
base = json.loads(Path('artifacts/a52xq-pinctrl-stage-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
gpio = (root / 'stage/gpiolib-after-phase189.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-gpiolib-stage-trace-audited',
    'phase': 189,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase188': False,
    'gpiolib_stage_trace_added': all(x in gpio for x in (
        'GPIOCORE add enter', 'GPIOCORE of-add enter',
        'GPIOCORE dir-read enter pin=%u', 'GPIOCORE irq-add enter',
        'GPIOCORE add success')),
    'eager_direction_read_behavior_preserved':
        'direction = gc->get_direction(gc, i);' in gpio,
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
    'gpiolib_stage_trace_added', 'eager_direction_read_behavior_preserved',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
