#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-display-really-probe-bypass"
OUT="$PWD/artifacts/a52xq-dsi-ctrl-probe-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT"/logs
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct and validate the complete phase-181 candidate first. Phase 182
# changes only diagnostic logging around the existing display-only bypass.
bash scripts/181_ci_v2.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{logs,stage,compile,package,tools}

cp "$ROOT/drivers/base/core.c" "$OUT/stage/core-before-phase182.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" "$OUT/stage/dsi-ctrl-before-phase182.c"
python3 scripts/182_apply.py --root "$ROOT" | tee "$OUT/logs/phase182-apply.log"
cp "$ROOT/drivers/base/core.c" "$OUT/stage/core-after-phase182.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" "$OUT/stage/dsi-ctrl-after-phase182.c"
cp scripts/182_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

for marker in \
  'DISP LINK begin' \
  'DISP LINK n=%u s=%s' \
  'DISP LINK n=%u of=%s' \
  'DISP LINK end' \
  'DISP CTRL step=match enter' \
  'DISP CTRL step=dts enter' \
  'DISP CTRL step=regmap enter' \
  'DISP CTRL step=clocks enter' \
  'DISP CTRL step=supplies enter' \
  'DISP CTRL step=catalog enter' \
  'DISP CTRL step=axi enter' \
  'DISP CTRL step=mdp enter' \
  'DISP CTRL step=list enter' \
  'DISP CTRL step=drvdata enter'; do
  grep -Fq "$marker" "$ROOT/drivers/base/core.c" "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
done

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase182-dsi-ctrl-probe-trace.patch"
test -s "$OUT/stage/phase182-dsi-ctrl-probe-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase182-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase182-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase182-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase182-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'CC      drivers/base/core.o' "$OUT/logs/phase182-compile.log"
grep -Fq 'CC      drivers/a52_display/msm/dsi/dsi_ctrl.o' "$OUT/logs/phase182-compile.log"
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
A52 GKI 5.10 phase 182 DSI controller probe trace

FLASH ONLY:
  package/boot.img -> BOOT partition

This candidate preserves the phase-181 display-only supplier-gate bypass. It
adds diagnostic records for every managed supplier link before the bypass
keeps or drops it, including supplier device, OF path, driver, status and
flags. It also records entry and exit around each major operation inside
dsi_ctrl_dev_probe(): match, allocations, DT parsing, regmap, clocks,
voltage supplies, catalog, AXI, MDP mapping, list insertion and drvdata.

No supplier decision, panel command, display timing, display mode, regulator
value or ESD policy is changed by phase 182.

After testing, collect the untouched raw 1 MiB RAMOOPS ZIP.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-dsi-ctrl-probe-trace')
base = json.loads(Path('artifacts/a52xq-display-really-probe-bypass/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-dsi-ctrl-probe-trace-audited',
    'phase': 182,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase181': False,
    'phase181_supplier_bypass_preserved': True,
    'supplier_link_identity_capture': [
        'supplier-device', 'supplier-of-path', 'supplier-driver',
        'link-status', 'link-flags', 'keep-or-drop'
    ],
    'dsi_ctrl_probe_stages': [
        'match', 'item-allocation', 'controller-allocation', 'state-init',
        'dts-parse', 'regmap', 'clocks', 'supplies', 'catalog', 'axi',
        'mdp-map', 'list-add', 'drvdata'
    ],
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
