#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-amoled-power-chain"
OUT="$PWD/artifacts/a52xq-display-defer-safety"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct phase 186 exactly, including the PDC and AMOLED provider fixes.
bash scripts/186_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools,hardware-evidence}

cp "$BUILD/.config" "$OUT/config/before-phase187.config"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-before-phase187.c"
cp "$ROOT/drivers/a52_display/180_a52_display_bind_audit.c" \
  "$OUT/stage/display-bind-audit-before-phase187.c"

python3 scripts/187_apply.py --root "$ROOT" | tee "$OUT/logs/phase187-apply.log"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-after-phase187.c"
cp "$ROOT/drivers/a52_display/180_a52_display_bind_audit.c" \
  "$OUT/stage/display-bind-audit-after-phase187.c"
cp scripts/187_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

# Phase 187 changes source behavior only. Preserve the complete phase-186 config.
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase187.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path

dd = Path('gki/common/drivers/base/dd.c').read_text()
audit = Path('gki/common/drivers/a52_display/180_a52_display_bind_audit.c').read_text()

assert 'DISP RP bypass' not in dd
assert 'ret = 0;\n\t}\n\tif (ret == -EPROBE_DEFER)\n\t\tdriver_deferred_probe_add_trigger' not in dd
assert 'DISP RP defer-preserved' in dd

start = dd.index('static bool a52_legacy_fw_devlink_consumer')
end = dd.index('static bool a52_legacy_ufs_named_reset_pinctrl', start)
legacy = dd[start:end]
assert '1d84000.ufshc' in legacy
assert 'f100000.pinctrl' not in legacy

assert 'a52_device_links_force_probe' not in audit
assert 'device_attach(' not in audit
assert 'DISP RETRY' not in audit
assert 'retry=normal,force' not in audit
assert 'DISP CORE phase=187 audit=read-only' in audit
PY

for symbol in \
  CONFIG_SPMI_MSM_PMIC_ARB=y \
  CONFIG_MFD_SPMI_PMIC=y \
  CONFIG_REGULATOR_QPNP_AMOLED=y \
  CONFIG_QCOM_PDC=y \
  CONFIG_PINCTRL_LAGOON=y \
  CONFIG_DISP_CC_LAGOON=y; do
  grep -Fqx "$symbol" "$BUILD/.config"
done

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase187-display-defer-safety.patch"
test -s "$OUT/stage/phase187-display-defer-safety.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase187-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase187-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase187-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase187-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'CC      drivers/base/dd.o' "$OUT/logs/phase187-compile.log"
grep -Fq 'CC      drivers/a52_display/180_a52_display_bind_audit.o' \
  "$OUT/logs/phase187-compile.log"

for marker in \
  'DISP RP defer-preserved' \
  'DISP CORE phase=187 audit=read-only' \
  'qcom,lagoon-pdc' \
  'qcom,qpnp-amoled-regulator' \
  'AMOLED probe exit'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done
for forbidden in \
  'DISP RP bypass' \
  'DISP CORE phase=180 audit=start retry=normal,force'; do
  if grep -aFq "$forbidden" "$BUILD/arch/arm64/boot/Image"; then
    echo "forbidden phase187 image marker remains: $forbidden" >&2
    exit 1
  fi
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"

python3 scripts/38_repack_a52_p1_boot.py \
  --source source/extracted/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

cat > "$OUT/hardware-evidence/PHASE186-REGRESSION.txt" <<'EOF'
Phase 186 hardware regression evidence

User-observed result:
  - early kernel panic roughly five seconds after boot
  - restoring a previous kernel image alone did not recover the installation
  - dirty-flashing the custom ROM was required

Recovered phase-186 recorder sequence:
  - f100000.pinctrl dropped unresolved supplier b220000.interrupt-controller
  - Lagoon PDC later probed successfully, rc=0
  - QPNP AMOLED regmap, DT parsing and OLEDB/AB/IBB registration succeeded, rc=0
  - dsi-display-primary supplier check returned -EPROBE_DEFER
  - its pinctrl supplier f100000.pinctrl was unbound and forcibly dropped
  - the diagnostic path recorded "DISP RP bypass", changed ret to 0 and completed
    really_probe with no bound display driver

The exact later panic stack was not present in the preserved pstore dmesg record.
Phase 187 therefore removes the directly recorded invalid forced-success path rather
than claiming a specific unrecorded faulting instruction.
EOF

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 187 display deferred-probe safety candidate

IMPORTANT:
  Phase 186 caused an early hardware panic and has been withdrawn.

FLASHABLE FILE, ONLY AFTER REVIEW:
  package/boot.img -> BOOT partition

Phase 187 preserves the working-source PDC and AMOLED provider additions, but removes
all inherited display supplier bypass behavior:
  - TLMM is no longer treated as a legacy fw_devlink override target
  - display -EPROBE_DEFER results remain deferred
  - no display supplier device link is dropped
  - the display audit is read-only and never calls device_attach
  - the UFS-only legacy workaround is preserved unchanged
  - no DTB, panel command, timing, refresh mode, voltage, ramdisk or recovery DTBO changes

This artifact is compile-audited and NOT hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('artifacts/a52xq-display-defer-safety')
base = json.loads(Path('artifacts/a52xq-amoled-power-chain/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
config_before = (root / 'config/before-phase187.config').read_bytes()
config_after = (root / 'config/final.config').read_bytes()
dd = (root / 'stage/dd-after-phase187.c').read_text()
audit_source = (root / 'stage/display-bind-audit-after-phase187.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
legacy = dd[dd.index('static bool a52_legacy_fw_devlink_consumer'):
            dd.index('static bool a52_legacy_ufs_named_reset_pinctrl')]

audit = dict(base)
audit.update({
    'status': 'a52-display-defer-safety-audited',
    'phase': 187,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase186_hardware_regression': True,
    'phase186_user_observed_panic_seconds_approx': 5,
    'phase186_exact_panic_stack_captured': False,
    'display_supplier_bypass_removed': 'DISP RP bypass' not in dd,
    'display_defer_preserved': 'DISP RP defer-preserved' in dd,
    'pinctrl_legacy_supplier_override_removed': 'f100000.pinctrl' not in legacy,
    'ufs_legacy_supplier_override_preserved': '1d84000.ufshc' in legacy,
    'display_audit_read_only': (
        'DISP CORE phase=187 audit=read-only' in audit_source and
        'a52_device_links_force_probe' not in audit_source and
        'device_attach(' not in audit_source),
    'phase186_configuration_preserved': config_before == config_after,
    'pdc_fix_preserved': b'qcom,lagoon-pdc' in image.read_bytes(),
    'amoled_provider_preserved': b'qcom,qpnp-amoled-regulator' in image.read_bytes(),
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
    'display_supplier_bypass_removed', 'display_defer_preserved',
    'pinctrl_legacy_supplier_override_removed',
    'ufs_legacy_supplier_override_preserved', 'display_audit_read_only',
    'phase186_configuration_preserved', 'pdc_fix_preserved',
    'amoled_provider_preserved', 'dtb_preserved', 'ramdisk_preserved',
    'recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
