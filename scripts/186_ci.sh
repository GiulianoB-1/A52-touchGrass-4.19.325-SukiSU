#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-pdc-lagoon-compat"
OUT="$PWD/artifacts/a52xq-amoled-power-chain"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct phase 185 first so this candidate contains the proven PDC fix.
bash scripts/185_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

test -d "$TG/.git"
test "$(git -C "$TG" rev-parse HEAD)" = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
test -f "$TG/drivers/regulator/qpnp-amoled-regulator.c"

cp "$BUILD/.config" "$OUT/config/before-phase186.config"
cp "$ROOT/drivers/regulator/Kconfig" "$OUT/stage/regulator-Kconfig-before-phase186"
cp "$ROOT/drivers/regulator/Makefile" "$OUT/stage/regulator-Makefile-before-phase186"
cp "$ROOT/drivers/mfd/qcom-spmi-pmic.c" "$OUT/stage/qcom-spmi-pmic-reference.c"
cp "$ROOT/drivers/spmi/spmi-pmic-arb.c" "$OUT/stage/spmi-pmic-arb-reference.c"
cp "$TG/drivers/regulator/qpnp-amoled-regulator.c" "$OUT/stage/qpnp-amoled-regulator-touchgrass.c"

python3 scripts/186_apply.py --root "$ROOT" --touchgrass "$TG" | tee "$OUT/logs/phase186-apply.log"
cp "$ROOT/drivers/regulator/qpnp-amoled-regulator.c" "$OUT/stage/qpnp-amoled-regulator-after-phase186.c"
cp "$ROOT/drivers/regulator/Kconfig" "$OUT/stage/regulator-Kconfig-after-phase186"
cp "$ROOT/drivers/regulator/Makefile" "$OUT/stage/regulator-Makefile-after-phase186"
cp scripts/186_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

"$ROOT/scripts/config" --file "$BUILD/.config" \
  --enable SPMI_MSM_PMIC_ARB \
  --enable MFD_SPMI_PMIC \
  --enable REGULATOR_QPNP_AMOLED

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase186-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"

for symbol in \
  CONFIG_SPMI=y \
  CONFIG_SPMI_MSM_PMIC_ARB=y \
  CONFIG_MFD_SPMI_PMIC=y \
  CONFIG_REGMAP_SPMI=y \
  CONFIG_REGULATOR_QPNP_AMOLED=y \
  CONFIG_QCOM_PDC=y \
  CONFIG_PINCTRL_LAGOON=y \
  CONFIG_DISP_CC_LAGOON=y; do
  grep -Fqx "$symbol" "$BUILD/.config"
done

for marker in \
  'qcom,qpnp-amoled-regulator' \
  'AMOLED probe enter' \
  'AMOLED probe stage=regmap' \
  'AMOLED probe stage=parse-dt' \
  'AMOLED probe stage=register-rails' \
  'AMOLED probe exit'; do
  grep -Fq "$marker" "$ROOT/drivers/regulator/qpnp-amoled-regulator.c"
done

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase186-amoled-power-chain.patch"
test -s "$OUT/stage/phase186-amoled-power-chain.patch"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase186-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase186-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase186-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase186-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in \
  'CC      drivers/spmi/spmi-pmic-arb.o' \
  'CC      drivers/mfd/qcom-spmi-pmic.o' \
  'CC      drivers/regulator/qpnp-amoled-regulator.o'; do
  grep -Fq "$object" "$OUT/logs/phase186-compile.log"
done
for marker in \
  'qcom,lagoon-pdc' \
  'qcom,spmi-pmic' \
  'qcom,qpnp-amoled-regulator' \
  'AMOLED probe enter' \
  'AMOLED probe exit'; do
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
A52 GKI 5.10 phase 186 AMOLED power-chain candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

TouchGrass comparison found that the A52 primary DSI display consumes AB and
IBB AMOLED bias rails supplied by compatible "qcom,qpnp-amoled-regulator".
The phase-185 image had SPMI core support but did not build the PMIC arbiter,
the SPMI PMIC parent, or the AMOLED regulator provider.

Phase 186:
  - preserves the phase-185 Lagoon PDC compatibility fix
  - enables the existing 5.10 Qualcomm SPMI PMIC arbiter
  - enables the existing 5.10 Qualcomm SPMI PMIC parent/regmap provider
  - ports the exact TouchGrass qpnp-amoled-regulator driver
  - registers OLEDB, AB and IBB regulator rails through the normal framework
  - records AMOLED probe stages in the existing RAMOOPS recorder
  - changes no DTB, panel command, timing, refresh mode or regulator voltage
  - adds no supplier bypass

This artifact is compile-audited, not hardware validated. After testing,
collect the untouched raw 1 MiB RAMOOPS ZIP before flashing another kernel.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-amoled-power-chain')
base = json.loads(Path('artifacts/a52xq-pdc-lagoon-compat/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
config = (root / 'config/final.config').read_text()
source = (root / 'stage/qpnp-amoled-regulator-after-phase186.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-amoled-power-chain-audited',
    'phase': 186,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase185': True,
    'root_cause_hypothesis': 'A52 AMOLED AB/IBB supply provider chain was absent from phase 185',
    'touchgrass_reference_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'phase185_reference_commit': '05d416b3cbf0676fb16540181d754a977e272527',
    'spmi_pmic_arb_enabled': 'CONFIG_SPMI_MSM_PMIC_ARB=y' in config,
    'mfd_spmi_pmic_enabled': 'CONFIG_MFD_SPMI_PMIC=y' in config,
    'regmap_spmi_enabled': 'CONFIG_REGMAP_SPMI=y' in config,
    'qpnp_amoled_enabled': 'CONFIG_REGULATOR_QPNP_AMOLED=y' in config,
    'qpnp_amoled_compatible_present': 'qcom,qpnp-amoled-regulator' in source,
    'amoled_probe_trace_added': all(x in source for x in (
        'AMOLED probe enter', 'AMOLED probe stage=regmap',
        'AMOLED probe stage=parse-dt', 'AMOLED probe stage=register-rails',
        'AMOLED probe exit')),
    'pdc_fix_preserved': 'qcom,lagoon-pdc' in image.read_bytes().decode('latin1'),
    'display_supplier_bypass_added': False,
    'pinctrl_supplier_bypass_added': False,
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
for key in ('spmi_pmic_arb_enabled', 'mfd_spmi_pmic_enabled',
            'regmap_spmi_enabled', 'qpnp_amoled_enabled',
            'qpnp_amoled_compatible_present', 'amoled_probe_trace_added',
            'pdc_fix_preserved', 'dtb_preserved', 'ramdisk_preserved',
            'recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
