#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_SCRIPT="$SCRIPT_DIR/11_qemu_full_stack_stress.sh"
RUNTIME_SCRIPT="$SCRIPT_DIR/.11_qemu_full_stack_stress.scoped.$$"

cleanup() {
  rm -f "$RUNTIME_SCRIPT"
}
trap cleanup EXIT

test -f "$BASE_SCRIPT" || {
  printf 'Missing base QEMU stress script: %s\n' "$BASE_SCRIPT" >&2
  exit 1
}

cp "$BASE_SCRIPT" "$RUNTIME_SCRIPT"

python3 - "$RUNTIME_SCRIPT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    """  SECURITY \\
  SECURITYFS \\
  SECURITY_SELINUX \\
""",
    """  IPV6 \\
  SAMSUNG_PRODUCT_SHIP \\
  SECURITY \\
  SECURITY_NETWORK \\
  SECURITYFS \\
  SECURITY_SELINUX \\
""",
    "enable generic networking and SELinux dependencies",
)

replace_once(
    """  FANOTIFY \\
  SCHED_WALT \\
""",
    """  FANOTIFY \\
  TASKSTATS \\
  ARCH_QCOM \\
  COMMON_CLK_QCOM \\
  SEC_PM \\
  SEC_MM \\
  DRM \\
  FB \\
  MEDIA_SUPPORT \\
  SOUND \\
  SND \\
  USB \\
  MMC \\
  ATA \\
  SCSI \\
  NETFILTER \\
  SCHED_WALT \\
""",
    "extend unused QEMU subsystem disable list",
)

replace_once(
    """config_value HZ 250
config_value DEFAULT_HUNG_TASK_TIMEOUT 60
""",
    """config_value HZ 250
config_value DEFAULT_HUNG_TASK_TIMEOUT 60
config_value SECURITY_SELINUX_SIDTAB_HASH_BITS 9
""",
    "pin SELinux sidtab hash size",
)

replace_once(
    """(MODULES|EXT4_FS|KPROBES|KSU|PROVE_LOCKING|KASAN|KVM|KEXEC|CRASH_DUMP|NFS_FS|TRANSPARENT_HUGEPAGE|FANOTIFY|SCHED_WALT)""",
    """(MODULES|EXT4_FS|KPROBES|KSU|PROVE_LOCKING|KASAN|IPV6|SAMSUNG_PRODUCT_SHIP|KVM|KEXEC|CRASH_DUMP|NFS_FS|TRANSPARENT_HUGEPAGE|FANOTIFY|TASKSTATS|ARCH_QCOM|COMMON_CLK_QCOM|SEC_PM|SEC_MM|SOC_BUS|INPUT|HID|DRM|FB|MEDIA_SUPPORT|SOUND|SND|USB|MMC|ATA|SCSI|NETFILTER|SECURITY_NETWORK|NETWORK_SECMARK|SECURITY_SELINUX|SECURITY_SELINUX_SIDTAB_HASH_BITS|SCHED_WALT)""",
    "extend QEMU key-symbol diagnostics",
)

replace_once(
    """  EXT4_FS \\
  KSU \\
""",
    """  EXT4_FS \\
  IPV6 \\
  SAMSUNG_PRODUCT_SHIP \\
  SECURITY_NETWORK \\
  NETWORK_SECMARK \\
  SECURITY_SELINUX \\
  KSU \\
""",
    "require resolved generic network and SELinux dependencies",
)

replace_once(
    """fi
for disabled in KVM KEXEC CRASH_DUMP NFS_FS TRANSPARENT_HUGEPAGE FANOTIFY SCHED_WALT; do
""",
    """fi
grep -Fq 'CONFIG_SECURITY_SELINUX_SIDTAB_HASH_BITS=9' "$QEMU_CONFIG" \\
  || fail "QEMU config did not retain CONFIG_SECURITY_SELINUX_SIDTAB_HASH_BITS=9"
for disabled in KVM KEXEC CRASH_DUMP NFS_FS TRANSPARENT_HUGEPAGE FANOTIFY TASKSTATS ARCH_QCOM COMMON_CLK_QCOM SEC_PM SEC_MM DRM FB MEDIA_SUPPORT SOUND SND USB MMC ATA SCSI NETFILTER SCHED_WALT; do
""",
    "validate resolved SELinux sidtab and disabled subsystems",
)

replace_once(
    """info "Generating generic ARM64 virt config from the exact patched source tree"
""",
    """info "Restoring standard skb fragment release for the generic QEMU build"
skbuff_source="$KERNEL_DIR/net/core/skbuff.c"
python3 - "$skbuff_source" <<'PY_SKBUFF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old_decl = '''extern int ipa3_add_pool_page(struct page *page);

'''
old_release = '''\tfor (i = 0; i < shinfo->nr_frags; i++) {
\t\tif (ipa3_add_pool_page(shinfo->frags[i].page.p) < 0)
\t\t\t__skb_frag_unref(&shinfo->frags[i]);
\t}
'''
new_release = '''\tfor (i = 0; i < shinfo->nr_frags; i++)
\t\t__skb_frag_unref(&shinfo->frags[i]);
'''
if text.count(old_decl) != 1:
    raise SystemExit(f"IPA skb pool declaration: expected one match, found {text.count(old_decl)}")
if text.count(old_release) != 1:
    raise SystemExit(f"IPA skb fragment release: expected one match, found {text.count(old_release)}")
text = text.replace(old_decl, '', 1)
text = text.replace(old_release, new_release, 1)
path.write_text(text)
PY_SKBUFF
! grep -Fq 'ipa3_add_pool_page' "$skbuff_source" \\
  || fail "QEMU skb fragment release still references the phone IPA pool"
grep -Fq '__skb_frag_unref(&shinfo->frags[i]);' "$skbuff_source" \\
  || fail "Standard QEMU skb fragment release is missing"
git -C "$KERNEL_DIR" diff --check -- net/core/skbuff.c
git -C "$KERNEL_DIR" diff -- net/core/skbuff.c \\
  > "$QEMU_ARTIFACT_DIR/qemu-skb-fragment-release-compat.patch"
test -s "$QEMU_ARTIFACT_DIR/qemu-skb-fragment-release-compat.patch" \\
  || fail "QEMU skb fragment release compatibility patch is empty"

info "Gating Qualcomm socinfo on ARCH_QCOM for the generic QEMU build"
qcom_soc_makefile="$KERNEL_DIR/drivers/soc/qcom/Makefile"
python3 - "$qcom_soc_makefile" <<'PY_QCOM_SOCINFO'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = 'obj-$(CONFIG_SOC_BUS) += socinfo.o\n'
new = 'obj-$(CONFIG_ARCH_QCOM) += socinfo.o\n'
count = text.count(old)
if count != 1:
    raise SystemExit(f"QCOM socinfo Makefile gate: expected one match, found {count}")
path.write_text(text.replace(old, new, 1))
PY_QCOM_SOCINFO
grep -Fq 'obj-$(CONFIG_ARCH_QCOM) += socinfo.o' "$qcom_soc_makefile" \\
  || fail "QCOM socinfo is not gated by ARCH_QCOM"
! grep -Fq 'obj-$(CONFIG_SOC_BUS) += socinfo.o' "$qcom_soc_makefile" \\
  || fail "QCOM socinfo still follows generic SOC_BUS"
git -C "$KERNEL_DIR" diff --check -- drivers/soc/qcom/Makefile
git -C "$KERNEL_DIR" diff -- drivers/soc/qcom/Makefile \\
  > "$QEMU_ARTIFACT_DIR/qemu-qcom-socinfo-gate.patch"
test -s "$QEMU_ARTIFACT_DIR/qemu-qcom-socinfo-gate.patch" \\
  || fail "QCOM socinfo gate patch is empty"

info "Fixing disabled QPNP power-on fallback linkage for the generic QEMU build"
qpnp_pon_header="$KERNEL_DIR/include/linux/input/qpnp-power-on.h"
python3 - "$qpnp_pon_header" <<'PY_QPNP_PON'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
# This snippet is embedded in an outer triple-double-quoted string.
old = '''int qpnp_pon_wd_config(bool enable)
{
\treturn -ENODEV;
}
'''
new = '''static inline int qpnp_pon_wd_config(bool enable)
{
\treturn -ENODEV;
}
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"disabled qpnp_pon_wd_config fallback: expected one match, found {count}")
path.write_text(text.replace(old, new, 1))
PY_QPNP_PON
grep -Fq 'static inline int qpnp_pon_wd_config(bool enable)' "$qpnp_pon_header" \\
  || fail "QPNP power-on fallback linkage fix is missing"
git -C "$KERNEL_DIR" diff --check -- include/linux/input/qpnp-power-on.h
git -C "$KERNEL_DIR" diff -- include/linux/input/qpnp-power-on.h \\
  > "$QEMU_ARTIFACT_DIR/qemu-qpnp-pon-fallback-compat.patch"
test -s "$QEMU_ARTIFACT_DIR/qemu-qpnp-pon-fallback-compat.patch" \\
  || fail "QPNP power-on fallback compatibility patch is empty"

info "Generating generic ARM64 virt config from the exact patched source tree"
""",
    "prune phone-only linker dependencies from the generic QEMU build",
)

replace_once(
    """info "Building generic ARM64 QEMU kernel with $PROFILE diagnostics"
""",
    """info "Generating SELinux headers for the generic QEMU output tree"
make -C "$KERNEL_DIR" O="$QEMU_OUT" \\
  DTC_EXT="$KERNEL_DIR/tools/dtc" \\
  scripts/selinux/genheaders/
qemu_genheaders="$QEMU_OUT/scripts/selinux/genheaders/genheaders"
test -x "$qemu_genheaders" || fail "QEMU SELinux genheaders tool is missing"
mkdir -p "$QEMU_OUT/security/selinux"
"$qemu_genheaders" \\
  "$QEMU_OUT/security/selinux/flask.h" \\
  "$QEMU_OUT/security/selinux/av_permissions.h"
test -s "$QEMU_OUT/security/selinux/flask.h" || fail "QEMU SELinux flask.h was not generated"
test -s "$QEMU_OUT/security/selinux/av_permissions.h" || fail "QEMU SELinux av_permissions.h was not generated"
sha256sum \\
  "$QEMU_OUT/security/selinux/flask.h" \\
  "$QEMU_OUT/security/selinux/av_permissions.h" \\
  > "$QEMU_ARTIFACT_DIR/qemu-selinux-generated-headers.sha256"

info "Building generic ARM64 QEMU kernel with $PROFILE diagnostics"
""",
    "generate SELinux headers before the generic QEMU Image build",
)

path.write_text(text)
PY

chmod +x "$RUNTIME_SCRIPT"
bash -n "$RUNTIME_SCRIPT"
grep -Fq '  TASKSTATS \' "$RUNTIME_SCRIPT"
grep -Fq '  ARCH_QCOM \' "$RUNTIME_SCRIPT"
grep -Fq '  COMMON_CLK_QCOM \' "$RUNTIME_SCRIPT"
grep -Fq '  SECURITY_NETWORK \' "$RUNTIME_SCRIPT"
grep -Fq '  NETWORK_SECMARK \' "$RUNTIME_SCRIPT"
grep -Fq '  SECURITY_SELINUX \' "$RUNTIME_SCRIPT"
grep -Fq '  IPV6 \' "$RUNTIME_SCRIPT"
grep -Fq '  SAMSUNG_PRODUCT_SHIP \' "$RUNTIME_SCRIPT"
grep -Fq '  SEC_PM \' "$RUNTIME_SCRIPT"
grep -Fq '  SEC_MM \' "$RUNTIME_SCRIPT"
grep -Fq '  NETFILTER \' "$RUNTIME_SCRIPT"
grep -Fq 'CONFIG_SECURITY_SELINUX_SIDTAB_HASH_BITS=9' "$RUNTIME_SCRIPT"
grep -Fq '__skb_frag_unref(&shinfo->frags[i]);' "$RUNTIME_SCRIPT"
grep -Fq 'qemu-skb-fragment-release-compat.patch' "$RUNTIME_SCRIPT"
grep -Fq 'obj-$(CONFIG_ARCH_QCOM) += socinfo.o' "$RUNTIME_SCRIPT"
grep -Fq 'qemu-qcom-socinfo-gate.patch' "$RUNTIME_SCRIPT"
grep -Fq 'static inline int qpnp_pon_wd_config(bool enable)' "$RUNTIME_SCRIPT"
grep -Fq 'qemu-qpnp-pon-fallback-compat.patch' "$RUNTIME_SCRIPT"
grep -Fq 'qemu_genheaders="$QEMU_OUT/scripts/selinux/genheaders/genheaders"' "$RUNTIME_SCRIPT"
grep -Fq 'qemu-selinux-generated-headers.sha256' "$RUNTIME_SCRIPT"

"$RUNTIME_SCRIPT" "$@"
