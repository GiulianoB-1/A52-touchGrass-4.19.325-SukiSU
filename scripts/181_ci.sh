#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-display-bindcore-retry"
OUT="$PWD/artifacts/a52xq-display-really-probe-bypass"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT"/logs
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct and validate the complete phase-180 baseline first. This preserves
# the RS(32) recorder and the two independent persistent copies unchanged.
bash scripts/180_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{logs,stage,compile,package,tools}

cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-before-phase181.c"
python3 scripts/181_apply.py --root "$ROOT" | tee "$OUT/logs/phase181-apply.log"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-after-phase181.c"
cp scripts/181_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

grep -Fq 'a52_display_probe_device' "$ROOT/drivers/base/dd.c"
grep -Fq 'DISP RP suppliers' "$ROOT/drivers/base/dd.c"
grep -Fq 'DISP RP bypass' "$ROOT/drivers/base/dd.c"
grep -Fq 'a52_device_links_force_probe(dev, &kept, &dropped);' "$ROOT/drivers/base/dd.c"
grep -Fq 'DISP RP pinctrl' "$ROOT/drivers/base/dd.c"
grep -Fq 'DISP RP dma' "$ROOT/drivers/base/dd.c"
grep -Fq 'DISP RP busprobe enter' "$ROOT/drivers/base/dd.c"

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase181-display-really-probe-source.patch"
test -s "$OUT/stage/phase181-display-really-probe-source.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase181-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase181-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase181-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase181-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'CC      drivers/base/dd.o' "$OUT/logs/phase181-compile.log"
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

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 181 display really_probe gate test

FLASH ONLY:
  package/boot.img -> BOOT partition

This candidate keeps Reed-Solomon protection and two independent recorder
copies. It records each generic pre-probe stage for the SDE, DSI display and
DSI controller devices. When the generic supplier check returns EPROBE_DEFER,
it performs the display-only supplier-link preparation from inside
really_probe(), where the helper was designed to run, then continues to
pinctrl, DMA and the real driver probe.

Panel commands, panel timing, display modes and ESD policy are unchanged.
After testing, collect the untouched raw 1 MiB RAMOOPS ZIP.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-display-really-probe-bypass')
base = json.loads(Path('artifacts/a52xq-display-bindcore-retry/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-display-really-probe-bypass-audited',
    'phase': 181,
    'hardware_validated': False,
    'flashable_candidate': True,
    'error_correction': 'reed-solomon-32-parity-symbols',
    'independent_copies': 2,
    'crc32': False,
    'driver_match_proven': True,
    'normal_attach_result': -517,
    'really_probe_supplier_gate_bypass': 'display-only-on-EPROBE_DEFER',
    'preprobe_stage_capture': [
        'supplier-check', 'pinctrl', 'dma-configure', 'driver-sysfs',
        'pm-domain', 'bus-probe', 'driver-probe', 'completion'
    ],
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'esd_policy_changed': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
assert audit['dtb_preserved'] is True
assert audit['ramdisk_preserved'] is True
assert audit['recovery_dtbo_preserved'] is True
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
