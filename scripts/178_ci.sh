#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-display-init-recorder-plain"
OUT="$PWD/artifacts/a52xq-gki510-fwdevlink-off"
TOOL="$PWD/tools/patch-a52-gki510-boot.py"

bash scripts/177_ci_exact.sh

test -s "$BASE_OUT/package/boot.img"
test -s "$TOOL"
python3 -m py_compile "$TOOL"

rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"

mv "$OUT/package/boot.img" "$OUT/package/boot-pre-fwdevlink-off.img"
python3 "$TOOL" \
  --input "$OUT/package/boot-pre-fwdevlink-off.img" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/fwdevlink-off-repack-report.json" \
  --append-cmdline 'fw_devlink=off'

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('artifacts/a52xq-gki510-fwdevlink-off')
boot = root / 'package/boot.img'
report = json.loads((root / 'package/fwdevlink-off-repack-report.json').read_text())
audit_path = root / 'final-audit.json'
audit = json.loads(audit_path.read_text())
audit.update({
    'status': 'a52-gki510-fwdevlink-off-display-recorder-boot-audited',
    'hardware_validated': False,
    'flashable_candidate': True,
    'test_hypothesis': 'fw_devlink prevents SDE, DSI display, and DSI controller probe callbacks from entering',
    'fw_devlink_mode': 'off',
    'samsung_footer_restored': True,
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'post_repack_invariants': report['invariants'],
})
audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 black-screen isolation candidate: fw_devlink=off

Mission: boot the Galaxy A52 on the Android 12 GKI 5.10 kernel.
Current blocker: the bootloader image disappears after roughly 20 seconds and
Android remains on a black screen.

The previous persistent trace showed:
  - the GKI 5.10 display drivers register successfully;
  - the DSI PHY binds;
  - qcom,sde-kms, qcom,dsi-display, and qcom,dsi-ctrl-hw-v2.4 remain unbound;
  - their probe callbacks are not observed;
  - panel prepare, DSI command transfer, and backlight are therefore not reached.

This candidate is intentionally narrow. Compared with the previous display
recorder image, it changes only the boot packaging metadata:
  1. appends fw_devlink=off to the kernel command line;
  2. restores the Samsung SEANDROIDENFORCE + four 0xff footer at the first
     page boundary after the DTB.

Kernel, ramdisk, DTB, external DTBO, recorder instrumentation, and display
port source are preserved from the previous candidate.

Flash package/boot.img to BOOT only. Do not flash
package/boot-pre-fwdevlink-off.img.

Success criterion:
  - the display remains active through Android startup and the lock screen or
    home screen becomes visible.

If it is still black, collect the same raw 1 MiB RAMOOPS archive before
restoring the working boot image.
EOF

rm -f "$OUT/SHA256SUMS"
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
