#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-amoled-power-chain"
OUT="$PWD/artifacts/a52xq-normal-display-defer"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct the complete phase-186 source first, including the proven Lagoon
# PDC match and the working TouchGrass AMOLED PMIC provider chain.
bash scripts/186_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase187.config"
cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-before-phase187.c"
cp "$ROOT/drivers/a52_secure/a52_display_bind_audit.c" "$OUT/stage/display-bind-audit-before-phase187.c"
cp "$ROOT/drivers/pinctrl/qcom/pinctrl-lagoon.c" "$OUT/stage/pinctrl-lagoon-before-phase187.c"

python3 scripts/187_apply.py --root "$ROOT" | tee "$OUT/logs/phase187-apply.log"

cp "$ROOT/drivers/base/dd.c" "$OUT/stage/dd-after-phase187.c"
cp "$ROOT/drivers/a52_secure/a52_display_bind_audit.c" "$OUT/stage/display-bind-audit-after-phase187.c"
cp "$ROOT/drivers/pinctrl/qcom/pinctrl-lagoon.c" "$OUT/stage/pinctrl-lagoon-after-phase187.c"
cp scripts/187_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

# This phase changes source behavior only. Preserve all phase-186 configuration,
# boot logging, PDC and AMOLED provider settings exactly.
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase187.config" "$OUT/config/final.config"

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

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
dd = (root / 'drivers/base/dd.c').read_text()
audit = (root / 'drivers/a52_secure/a52_display_bind_audit.c').read_text()
pinctrl = (root / 'drivers/pinctrl/qcom/pinctrl-lagoon.c').read_text()

assert 'return name && !strcmp(name, "1d84000.ufshc");' in dd
assert 'f100000.pinctrl"));' not in dd
assert 'DISP RP bypass' not in dd
assert 'DISP RP defer-normal' in dd
# One active force-probe call remains in really_probe, for UFS only.
assert dd.count('a52_device_links_force_probe(dev, &kept, &dropped);') == 1

assert 'a52_device_links_force_probe(&pdev->dev, &kept, &dropped);' not in audit
assert 'DISP CORE phase=187 audit=observe-only normal-defer' in audit
assert 'DISP CORE phase=187 observe-only pass=%u' in audit
assert 'retry_all(pass, false);' not in audit
assert 'retry_all(pass, true);' not in audit

assert 'PINCTRL Lagoon probe enter' in pinctrl
assert 'PINCTRL Lagoon probe exit' in pinctrl
PY

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase187-normal-display-defer.patch"
test -s "$OUT/stage/phase187-normal-display-defer.patch"

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
for object in \
  'drivers/base/dd.o' \
  'drivers/a52_secure/a52_display_bind_audit.o' \
  'drivers/pinctrl/qcom/pinctrl-lagoon.o'; do
  grep -Fq "$object" "$OUT/logs/phase187-compile.log"
done

for marker in \
  'qcom,lagoon-pdc' \
  'qcom,qpnp-amoled-regulator' \
  'DISP RP defer-normal' \
  'DISP CORE phase=187 audit=observe-only normal-defer' \
  'PINCTRL Lagoon probe enter' \
  'PINCTRL Lagoon probe exit'; do
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

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 187 normal deferred-probe candidate

DO NOT USE PHASE 186 AGAIN.

FLASH ONLY:
  package/boot.img -> BOOT partition

Hardware evidence from phase 186:
  - Lagoon PDC probe completed with rc=0
  - QPNP AMOLED parent regmap, DT parsing and OLEDB/AB/IBB registration all
    completed with rc=0
  - f100000.pinctrl remained unbound because inherited diagnostic code removed
    its unresolved PDC supplier link before PDC became available
  - inherited display diagnostics later removed the display-to-TLMM dependency
    and forced display registration to continue without pinctrl
  - the phone entered a visible kernel-panic state several seconds later

Phase 187:
  - preserves the successful PDC and AMOLED provider fixes
  - removes f100000.pinctrl from the legacy forced-supplier path
  - removes the display supplier bypass from really_probe()
  - disables the delayed forced display retry and leaves it observation-only
  - restores normal -EPROBE_DEFER handling so PDC binding can reprobe TLMM and
    TLMM binding can reprobe the DSI display naturally
  - traces the Lagoon pinctrl probe entry and result
  - leaves only the existing UFS forced-link exception required for boot storage
  - changes no DTB, panel command, timing, refresh mode or regulator voltage

This artifact is compile-audited, not hardware validated. Because phase 186
required a ROM dirty flash to recover, make a current recovery backup and keep
the exact ROM package available before testing another candidate.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-normal-display-defer')
base = json.loads(Path('artifacts/a52xq-amoled-power-chain/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
dd = (root / 'stage/dd-after-phase187.c').read_text()
audit_src = (root / 'stage/display-bind-audit-after-phase187.c').read_text()
pinctrl = (root / 'stage/pinctrl-lagoon-after-phase187.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-normal-display-defer-audited',
    'phase': 187,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase186': True,
    'phase186_hardware_result': 'visible kernel panic after approximately five seconds; exact panic text not preserved',
    'root_cause_evidence': 'inherited diagnostics dropped TLMM-to-PDC and display-to-TLMM managed supplier links',
    'pdc_fix_preserved': 'qcom,lagoon-pdc' in image.read_bytes().decode('latin1'),
    'amoled_fix_preserved': 'qcom,qpnp-amoled-regulator' in image.read_bytes().decode('latin1'),
    'tlmm_supplier_bypass_removed': 'f100000.pinctrl"));' not in dd,
    'display_supplier_bypass_removed': 'DISP RP bypass' not in dd,
    'normal_display_defer_marker_present': 'DISP RP defer-normal' in dd,
    'forced_display_retry_removed': 'a52_device_links_force_probe(&pdev->dev, &kept, &dropped);' not in audit_src,
    'display_audit_observe_only': 'phase=187 audit=observe-only normal-defer' in audit_src,
    'lagoon_pinctrl_probe_trace_added': all(x in pinctrl for x in (
        'PINCTRL Lagoon probe enter', 'PINCTRL Lagoon probe exit')),
    'ufs_forced_link_exception_preserved': dd.count('a52_device_links_force_probe(dev, &kept, &dropped);') == 1,
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
    'pdc_fix_preserved', 'amoled_fix_preserved',
    'tlmm_supplier_bypass_removed', 'display_supplier_bypass_removed',
    'normal_display_defer_marker_present', 'forced_display_retry_removed',
    'display_audit_observe_only', 'lagoon_pinctrl_probe_trace_added',
    'ufs_forced_link_exception_preserved', 'dtb_preserved',
    'ramdisk_preserved', 'recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
