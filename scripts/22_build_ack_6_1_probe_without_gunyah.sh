#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/21_build_ack_6_1_probe.sh"

test -f "$TARGET" || {
  echo "Missing ACK 6.1 probe build script: $TARGET" >&2
  exit 1
}

python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

anchor = '''# Keep the initial image self-contained.
"$cfg" "${config_args[@]}" --disable MODULES
'''
replacement = '''# GKI defconfig enables Gunyah virtualization. The first probe is a
# self-contained physical-device kernel with CONFIG_MODULES disabled, while
# Gunyah's VM manager still calls module_refcount(). Disable this unused
# virtualization stack before olddefconfig rather than re-enabling modules.
for symbol in \\
  GUNYAH GUNYAH_PLATFORM_HOOKS GUNYAH_QCOM_PLATFORM \\
  GUNYAH_VCPU GUNYAH_IRQFD GUNYAH_IOEVENTFD; do
  "$cfg" "${config_args[@]}" --disable "$symbol"
done

# Android debug kinfo records offsets from struct module. In this ACK revision
# that type is incomplete when CONFIG_MODULES is disabled, so the probe cannot
# compile with CONFIG_ANDROID_DEBUG_KINFO enabled. It is not needed for bring-up.
"$cfg" "${config_args[@]}" --disable ANDROID_DEBUG_KINFO

# Keep the initial image self-contained.
"$cfg" "${config_args[@]}" --disable MODULES
'''

count = text.count(anchor)
if count != 1:
    raise SystemExit(f"Gunyah config anchor: expected one match, found {count}")

text = text.replace(anchor, replacement, 1)

summary_old = "|PHY_QCOM_QMP_UFS|MODULES)=|# CONFIG_MODULES is not set)'"
summary_new = "|PHY_QCOM_QMP_UFS|ANDROID_DEBUG_KINFO|GUNYAH|MODULES)=|# CONFIG_(ANDROID_DEBUG_KINFO|GUNYAH|MODULES) is not set)'"
count = text.count(summary_old)
if count != 1:
    raise SystemExit(f"config summary anchor: expected one match, found {count}")
text = text.replace(summary_old, summary_new, 1)

assert_old = '''grep -Fqx '# CONFIG_MODULES is not set' "$FINAL_CONFIG" || fail "Probe kernel unexpectedly enables modules"
'''
assert_new = '''grep -Fqx '# CONFIG_ANDROID_DEBUG_KINFO is not set' "$FINAL_CONFIG" || fail "Probe kernel unexpectedly enables Android debug kinfo"
grep -Fqx '# CONFIG_GUNYAH is not set' "$FINAL_CONFIG" || fail "Probe kernel unexpectedly enables Gunyah"
grep -Fqx '# CONFIG_MODULES is not set' "$FINAL_CONFIG" || fail "Probe kernel unexpectedly enables modules"
'''
count = text.count(assert_old)
if count != 1:
    raise SystemExit(f"Gunyah assertion anchor: expected one match, found {count}")
text = text.replace(assert_old, assert_new, 1)

path.write_text(text)
PY

bash -n "$TARGET"
exec bash "$TARGET"
