#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-dispcc-probe-trace"
OUT="$PWD/artifacts/a52xq-pdc-lagoon-compat"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct the hardware-tested phase 183 source and build state first.
bash scripts/183_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$ROOT/drivers/irqchip/qcom-pdc.c" "$OUT/stage/qcom-pdc-before-phase185.c"
cp "$BUILD/.config" "$OUT/config/before-phase185.config"
python3 scripts/185_apply.py --root "$ROOT" | tee "$OUT/logs/phase185-apply.log"
cp "$ROOT/drivers/irqchip/qcom-pdc.c" "$OUT/stage/qcom-pdc-after-phase185.c"
cp scripts/185_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

# Phase 185 is a source compatibility correction only. Preserve the complete
# phase-183 configuration, including RAMOOPS and all display settings.
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase185.config" "$OUT/config/final.config"
grep -Fqx 'CONFIG_QCOM_PDC=y' "$BUILD/.config"
grep -Fqx 'CONFIG_PINCTRL_LAGOON=y' "$BUILD/.config"
grep -Fqx 'CONFIG_DISP_CC_LAGOON=y' "$BUILD/.config"

for marker in \
  'qcom,lagoon-pdc' \
  'PDC probe enter' \
  'PDC probe exit'; do
  grep -Fq "$marker" "$ROOT/drivers/irqchip/qcom-pdc.c"
done

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase185-pdc-lagoon-compat.patch"
test -s "$OUT/stage/phase185-pdc-lagoon-compat.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase185-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase185-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase185-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase185-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'CC      drivers/irqchip/qcom-pdc.o' "$OUT/logs/phase185-compile.log"
for marker in 'qcom,lagoon-pdc' 'PDC probe enter' 'PDC probe exit'; do
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

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 185 Lagoon PDC compatibility trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Evidence from the phase-183 hardware capture:
  - Lagoon display-clock-controller probe completed successfully
  - the DSI controller probe completed successfully
  - dsi-display-primary deferred in generic pinctrl setup
  - f100000.pinctrl depended on unbound b220000.interrupt-controller

TouchGrass source comparison found that the preserved Samsung DTB uses
compatible "qcom,lagoon-pdc". The generic Android 5.10 PDC platform driver only
matched "qcom,pdc", while the working TouchGrass kernel explicitly initializes
"qcom,lagoon-pdc".

Phase 185:
  - adds qcom,lagoon-pdc to the existing 5.10 PDC driver's match table
  - records PDC platform probe entry and result
  - preserves normal supplier deferral for TLMM and the display
  - changes no panel command, timing, mode, regulator setting or DTB
  - leaves the complete phase-183 PSTORE/RAMOOPS configuration unchanged

After testing, collect the untouched raw 1 MiB RAMOOPS ZIP before flashing
another kernel.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-pdc-lagoon-compat')
base = json.loads(Path('artifacts/a52xq-dispcc-probe-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
config = (root / 'config/final.config').read_text()
source = (root / 'stage/qcom-pdc-after-phase185.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-pdc-lagoon-compat-audited',
    'phase': 185,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase183': True,
    'root_cause_hypothesis': 'Samsung DTB qcom,lagoon-pdc compatible was unmatched by generic 5.10 PDC platform driver',
    'touchgrass_reference_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'phase183_reference_commit': 'a609d255be282311a86b94fd285a5dcbbf3935b0',
    'qcom_pdc_enabled': 'CONFIG_QCOM_PDC=y' in config,
    'pinctrl_lagoon_enabled': 'CONFIG_PINCTRL_LAGOON=y' in config,
    'disp_cc_lagoon_enabled': 'CONFIG_DISP_CC_LAGOON=y' in config,
    'lagoon_pdc_compatible_added': '{ .compatible = "qcom,lagoon-pdc" },' in source,
    'pdc_probe_trace_added': 'PDC probe enter' in source and 'PDC probe exit' in source,
    'pinctrl_supplier_bypass_added': False,
    'display_supplier_bypass_added': False,
    'dtb_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'display_modes_changed': False,
    'storage_write_added': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
assert audit['qcom_pdc_enabled'] is True
assert audit['pinctrl_lagoon_enabled'] is True
assert audit['disp_cc_lagoon_enabled'] is True
assert audit['lagoon_pdc_compatible_added'] is True
assert audit['pdc_probe_trace_added'] is True
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
