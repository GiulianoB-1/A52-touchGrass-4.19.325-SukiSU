#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-gpiolib-stage-trace"
OUT="$PWD/artifacts/a52xq-lagoon-fingerprint-gpio-reserve"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/189_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase190.config"
cp "$ROOT/drivers/pinctrl/qcom/pinctrl-lagoon.c" \
  "$OUT/stage/pinctrl-lagoon-before-phase190.c"

python3 scripts/190_apply.py --root "$ROOT" | tee "$OUT/logs/phase190-apply.log"

cp "$ROOT/drivers/pinctrl/qcom/pinctrl-lagoon.c" \
  "$OUT/stage/pinctrl-lagoon-after-phase190.c"
cp scripts/190_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase190.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
lagoon = (root / 'drivers/pinctrl/qcom/pinctrl-lagoon.c').read_text()
gpio = (root / 'drivers/gpio/gpiolib.c').read_text()
dd = (root / 'drivers/base/dd.c').read_text()
audit = (root / 'drivers/a52_secure/a52_display_bind_audit.c').read_text()
reserved = lagoon.split('static const int lagoon_reserved_gpios[] = {', 1)[1].split('};', 1)[0]
assert '13, 14, 15, 16,' in reserved
assert 'CONFIG_FINGERPRINT_SECURE' not in reserved
assert 'CONFIG_SEC_FACTORY' not in reserved
assert 'PINCTRL Lagoon reserved secure=13-16' in lagoon
assert 'GPIOCORE dir-read enter pin=%u' in gpio
assert 'direction = gc->get_direction(gc, i);' in gpio
assert 'DISP RP bypass' not in dd
assert dd.count('a52_device_links_force_probe(dev, &kept, &dropped);') == 1
assert 'retry_all(' not in audit
assert 'device_attach(' not in audit
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase190-lagoon-fingerprint-gpio-reserve.patch"
test -s "$OUT/stage/phase190-lagoon-fingerprint-gpio-reserve.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase190-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase190-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase190-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase190-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/pinctrl/qcom/pinctrl-lagoon.o' \
  "$OUT/logs/phase190-compile.log"

for marker in \
  'PINCTRL Lagoon reserved secure=13-16' \
  'GPIOCORE dir-scan enter' \
  'GPIOCORE dir-read enter pin=%u' \
  'GPIOCORE dir-read exit pin=%u rc=%d'; do
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
A52 GKI 5.10 phase 190 Lagoon secure-fingerprint GPIO reservation candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase-189 hardware evidence:
  - generic GPIO-core setup completed through valid-mask initialization
  - eager direction reads succeeded for GPIOs 0 through 12
  - the final persistent record was GPIOCORE dir-read enter pin=13
  - no matching pin=13 exit record was written

Phase 190 restores the vendor reservation for secure-fingerprint GPIOs 13-16
without relying on Samsung-only Kconfig symbols that are absent from the GKI
source tree. The phase-189 GPIO-core trace remains enabled.

Expected proof in the next RAMOOPS capture:
  - PINCTRL Lagoon reserved secure=13-16
  - GPIOCORE dir-read exit pin=12 rc=...
  - the scan skips pins 13-16
  - GPIOCORE dir-read enter pin=17

This candidate does not disable runtime get_direction(), does not skip the full
GPIO direction scan, and does not change the DTB, panel commands, display timing,
refresh modes, regulator voltages, ramdisk or recovery DTBO. Compile-audited,
not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-lagoon-fingerprint-gpio-reserve')
base = json.loads(Path('artifacts/a52xq-gpiolib-stage-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
lagoon = (root / 'stage/pinctrl-lagoon-after-phase190.c').read_text()
gpio = (root / 'stage/gpiolib-after-phase189.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
reserved = lagoon.split('static const int lagoon_reserved_gpios[] = {', 1)[1].split('};', 1)[0]
audit = dict(base)
audit.update({
    'status': 'a52-lagoon-fingerprint-gpio-reserve-audited',
    'phase': 190,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase189': True,
    'phase189_last_record': 'GPIOCORE dir-read enter pin=13',
    'secure_fingerprint_gpios_reserved':
        '13, 14, 15, 16,' in reserved and
        'CONFIG_FINGERPRINT_SECURE' not in reserved,
    'phase189_gpiolib_trace_preserved':
        'GPIOCORE dir-read enter pin=%u' in gpio,
    'runtime_get_direction_preserved':
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
    'secure_fingerprint_gpios_reserved',
    'phase189_gpiolib_trace_preserved',
    'runtime_get_direction_preserved',
    'dtb_preserved',
    'ramdisk_preserved',
    'recovery_dtbo_preserved',
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
