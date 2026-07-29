#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-dsi-ctrl-probe-trace"
OUT="$PWD/artifacts/a52xq-dispcc-probe-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct and validate phase 182 first. Phase 183 then enables the Lagoon
# display clock controller, instruments its registration/probe path, and stops
# forcing the DSI controller through unresolved supplier links.
bash scripts/182_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-before-phase183.c"
cp "$ROOT/drivers/clk/qcom/dispcc-lagoon.c" "$OUT/stage/dispcc-lagoon-before-phase183.c"
cp "$BUILD/.config" "$OUT/config/before-phase183.config"
python3 scripts/183_apply.py --root "$ROOT" | tee "$OUT/logs/phase183-apply.log"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-after-phase183.c"
cp "$ROOT/drivers/clk/qcom/dispcc-lagoon.c" "$OUT/stage/dispcc-lagoon-after-phase183.c"
cp scripts/183_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

# The phase-182 capture proved that the DSI controller consumes clocks from
# dispcc, but the final configuration did not build the Lagoon dispcc driver.
"$ROOT/scripts/config" --file "$BUILD/.config" --enable DISP_CC_LAGOON

# Enable block-backed pstore capability only. No blkdev, partition or size is
# configured, so this cannot write to storage in this candidate.
if grep -q '^config PSTORE_ZONE' "$ROOT/fs/pstore/Kconfig"; then
  "$ROOT/scripts/config" --file "$BUILD/.config" --enable PSTORE_ZONE
fi
if grep -q '^config PSTORE_BLK' "$ROOT/fs/pstore/Kconfig"; then
  "$ROOT/scripts/config" --file "$BUILD/.config" --enable PSTORE_BLK
fi

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase183-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"

grep -Fqx 'CONFIG_DISP_CC_LAGOON=y' "$BUILD/.config"
if grep -q '^config PSTORE_BLK' "$ROOT/fs/pstore/Kconfig"; then
  grep -Fqx 'CONFIG_PSTORE_BLK=y' "$BUILD/.config"
fi

for marker in \
  'DISP RP defer-preserved' \
  'DISPCC init enter' \
  'DISPCC probe enter' \
  'DISPCC step=map enter' \
  'DISPCC step=pll enter' \
  'DISPCC step=register enter' \
  'DISPCC probe done'; do
  grep -Fq "$marker" "$ROOT/drivers/base/dd.c" "$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
done

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase183-dispcc-probe-trace.patch"
test -s "$OUT/stage/phase183-dispcc-probe-trace.patch"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase183-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase183-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase183-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase183-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'CC      drivers/base/dd.o' "$OUT/logs/phase183-compile.log"
grep -Fq 'CC      drivers/clk/qcom/dispcc-lagoon.o' "$OUT/logs/phase183-compile.log"
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
A52 GKI 5.10 phase 183 Lagoon display-clock-controller trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 182 proved that drm_dsi_ctrl stops in dsi_ctrl_clocks_init() after its
unavailable qcom,dispcc supplier was dropped. The previous final config built
CONFIG_SDM_GCC_LAGOON but omitted CONFIG_DISP_CC_LAGOON.

Phase 183:
  - enables CONFIG_DISP_CC_LAGOON=y
  - records disp_cc-lagoon driver init and every major probe stage
  - preserves normal -EPROBE_DEFER handling for qcom,dsi-ctrl-hw-v2.4
  - does not drop the DSI controller's unresolved supplier links
  - enables pstore/blk capability when present, but configures no block target

No storage partition is written by this image. RAMOOPS remains the active
persistent recorder. After testing, collect the untouched raw 1 MiB RAMOOPS
ZIP before flashing another kernel.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-dispcc-probe-trace')
base = json.loads(Path('artifacts/a52xq-dsi-ctrl-probe-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
config = (root / 'config/final.config').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-dispcc-probe-trace-audited',
    'phase': 183,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase182': True,
    'root_cause_hypothesis': 'Lagoon display clock controller omitted from final config',
    'disp_cc_lagoon_enabled': 'CONFIG_DISP_CC_LAGOON=y' in config,
    'dsi_controller_supplier_bypass_disabled': True,
    'dsi_controller_normal_defer_preserved': True,
    'dispcc_probe_stages': [
        'driver-register', 'probe-entry', 'regmap',
        'pll-configure', 'clock-register', 'probe-complete'
    ],
    'pstore_blk_capability_enabled': 'CONFIG_PSTORE_BLK=y' in config,
    'pstore_block_target_configured': False,
    'storage_write_added': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'display_modes_changed': False,
    'esd_policy_changed': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
assert audit['disp_cc_lagoon_enabled'] is True
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
