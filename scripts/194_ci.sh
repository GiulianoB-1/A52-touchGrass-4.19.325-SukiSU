#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-rscc-drivercore-gate-trace"
OUT="$PWD/artifacts/a52xq-mdss-core-gdsc-provider"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/193_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase194.config"
cp "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  "$OUT/stage/a52-legacy-gdsc-before-phase194.c"

python3 scripts/194_apply.py --root "$ROOT" | tee "$OUT/logs/phase194-apply.log"

cp "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  "$OUT/stage/a52-legacy-gdsc-after-phase194.c"
cp scripts/194_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase194.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
gdsc = (root / 'drivers/regulator/a52-legacy-gdsc-regulator.c').read_text()
dd = (root / 'drivers/base/dd.c').read_text()
rsc = (root / 'drivers/a52_display/msm/sde_rsc.c').read_text()
for marker in (
    '"gcc_ufs_phy_gdsc"', '"mdss_core_gdsc"',
    'A52GDSC disable-keep-on profile=ufs',
    'A52GDSC disable profile=mdss',
    'A52GDSC mdss-init name=%s before=0x%x after=0x%x hw=%u',
    'A52GDSC register exit dev=%s name=%s rc=0 reg=0x%x profile=%s',
    'REGULATOR_CHANGE_MODE',
    'REGULATOR_MODE_NORMAL | REGULATOR_MODE_FAST',
    'val |= A52_GDSC_SW_COLLAPSE;',
    'val &= ~A52_GDSC_HW_CONTROL;',
):
    assert marker in gdsc, marker
assert 'A52GDSC DISABLE_KEEP_ON' in gdsc
assert 'return -ENODEV;' in gdsc
assert '.compatible = "qcom,gdsc"' in gdsc
assert 'devm_ioremap(&pdev->dev, res->start, resource_size(res))' in gdsc
assert 'RSCCCORE suppliers dev=%s rc=%d reason=%s' in dd
assert dd.count('ret = device_links_check_suppliers(dev);') == 1
assert 'RSCC probe enter dev=%s node=%s counter=%d rpmh=%u' in rsc
assert 'RSCC component-add exit rc=%d' in rsc
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase194-mdss-core-gdsc-provider.patch"
test -s "$OUT/stage/phase194-mdss-core-gdsc-provider.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase194-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase194-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase194-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase194-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/regulator/a52-legacy-gdsc-regulator.o' \
  "$OUT/logs/phase194-compile.log"
for marker in \
  'A52GDSC driver-register enter' \
  'A52GDSC mdss-init name=%s before=0x%x after=0x%x hw=%u' \
  'A52GDSC register enter dev=%s name=%s profile=%s hw=%u' \
  'A52GDSC enable profile=%s name=%s rc=%d before=0x%x after=0x%x' \
  'A52GDSC disable profile=mdss name=%s rc=%d before=0x%x after=0x%x' \
  'A52GDSC disable-keep-on profile=ufs name=%s reg=0x%x' \
  'RSCCCORE suppliers dev=%s rc=%d reason=%s' \
  'RSCC probe stage=vdd-enable rc=%d' \
  'RSCC component-add exit rc=%d' \
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
A52 GKI 5.10 phase 194 MDSS core GDSC provider candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 193 hardware result:
  - qcom,sde-rsc matches sde_rsc
  - driver-core probing begins normally
  - the display clock-controller supplier is ready
  - the mdss_core_gdsc supplier has no driver
  - supplier check returns -EPROBE_DEFER before sde_rsc_probe()
  - BOOT_READY was reached and the kernel stayed alive through 46.140115 seconds

Phase 194 is a narrow functional provider correction:
  - the existing hardware-validated UFS GDSC profile remains keep-on
  - mdss_core_gdsc is accepted as a second explicit profile
  - MDSS uses normal software enable and power collapse
  - the DT-declared hardware-trigger normal/fast modes are supported
  - the existing supplier link and deferred-probe ordering are preserved

It does not force probe, bypass a supplier, change the DTB, panel commands,
display timing, clock rates, regulator voltages, ramdisk, or recovery DTBO.
Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-mdss-core-gdsc-provider')
base = json.loads(Path('artifacts/a52xq-rscc-drivercore-gate-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
gdsc = (root / 'stage/a52-legacy-gdsc-after-phase194.c').read_text()
image = root / 'compile/Image'; boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-mdss-core-gdsc-provider-audited',
    'phase': 194,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase193': True,
    'phase193_hardware_validated': True,
    'phase193_kernel_alive_ms': 46140.115,
    'phase193_blocking_supplier': 'qcom,gdsc@af01004 / mdss_core_gdsc',
    'phase193_supplier_errno': -517,
    'ufs_profile_preserved': 'A52GDSC DISABLE_KEEP_ON' in gdsc,
    'mdss_profile_added': '"mdss_core_gdsc"' in gdsc,
    'mdss_normal_collapse_added': 'A52GDSC disable profile=mdss' in gdsc,
    'mdss_hw_trigger_mode_added': 'REGULATOR_CHANGE_MODE' in gdsc,
    'supplier_link_bypassed': False,
    'probe_forced': False,
    'dtb_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'display_modes_changed': False,
    'clock_rates_changed': False,
    'regulator_voltage_changed': False,
    'storage_write_added': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in ('phase193_hardware_validated','ufs_profile_preserved',
            'mdss_profile_added','mdss_normal_collapse_added',
            'mdss_hw_trigger_mode_added','dtb_preserved','ramdisk_preserved',
            'recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True)+'\n')
PY
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
