#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.159
TO_TAG=v$TARGET_VERSION
REPORT="$ARTIFACTS_DIR/reviewed-resolutions-$TO_TAG.txt"
ACTUAL="$ARTIFACTS_DIR/actual-rejects-$TO_TAG.txt"
EXPECTED="$ARTIFACTS_DIR/expected-rejects-$TO_TAG.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION after checkpoint apply"

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ACTUAL"
cat > "$EXPECTED" <<'EOF'
Documentation/filesystems/fscrypt.rst.rej
drivers/rpmsg/qcom_glink_native.c.rej
drivers/usb/dwc3/core.c.rej
drivers/usb/dwc3/gadget.c.rej
fs/crypto/crypto.c.rej
fs/crypto/fname.c.rej
fs/crypto/hooks.c.rej
fs/crypto/policy.c.rej
fs/dcache.c.rej
fs/exec.c.rej
fs/ext4/ext4.h.rej
fs/ext4/namei.c.rej
fs/f2fs/namei.c.rej
fs/fuse/dev.c.rej
fs/ubifs/dir.c.rej
include/linux/dcache.h.rej
include/linux/fscrypt.h.rej
kernel/reboot.c.rej
net/sched/sch_generic.c.rej
scripts/setlocalversion.rej
EOF
diff -u "$EXPECTED" "$ACTUAL" > "$ARTIFACTS_DIR/reject-set-$TO_TAG.diff" \
  || fail "Linux $TARGET_VERSION reject set changed; review is required"

info "Validating fixes already carried by the Android/vendor tree"
grep -Fq 'complete_all(&channel->open_ack);' "$KERNEL_DIR/drivers/rpmsg/qcom_glink_native.c" \
  || fail "GLINK open-ack complete_all fix is missing"
grep -Fq 'glink->intentless || !completion_done(&channel->open_ack)' "$KERNEL_DIR/drivers/rpmsg/qcom_glink_native.c" \
  || fail "GLINK open-ack readiness guard is missing"
grep -Fq 'fname->is_ciphertext_name = true;' "$KERNEL_DIR/fs/crypto/fname.c" \
  || fail "fscrypt ciphertext-name tracking is missing"
grep -Fq 'int fscrypt_d_revalidate(struct dentry *dentry, unsigned int flags)' "$KERNEL_DIR/fs/crypto/fname.c" \
  || fail "Vendor fscrypt dentry revalidation is missing"
test "$(grep -RFl --include='*.c' \
  'int fscrypt_d_revalidate(struct dentry *dentry, unsigned int flags)' \
  "$KERNEL_DIR/fs/crypto" | wc -l)" -eq 1 \
  || fail "fscrypt dentry revalidation must have exactly one implementation"
grep -Fq 'return -EXDEV;' "$KERNEL_DIR/fs/crypto/hooks.c" \
  || fail "fscrypt cross-policy EXDEV handling is missing"
grep -Fq 'dentry->d_flags |= DCACHE_ENCRYPTED_NAME;' "$KERNEL_DIR/fs/crypto/hooks.c" \
  || fail "fscrypt encrypted-name dentry flag is missing"
grep -Fq 'fscrypt_handle_d_move(dentry);' "$KERNEL_DIR/fs/dcache.c" \
  || fail "dcache encrypted-name move handling is missing"
grep -Fq '#define DCACHE_ENCRYPTED_NAME' "$KERNEL_DIR/include/linux/dcache.h" \
  || fail "DCACHE_ENCRYPTED_NAME is missing"
grep -Fq 'static inline void fscrypt_handle_d_move' "$KERNEL_DIR/include/linux/fscrypt.h" \
  || fail "fscrypt_handle_d_move helper is missing"
grep -Fq 'f2fs_prepare_lookup(dir, dentry, &fname);' "$KERNEL_DIR/fs/f2fs/namei.c" \
  || fail "F2FS encrypted lookup wrapper is missing"
grep -Fq 'f2fs_free_filename(&fname);' "$KERNEL_DIR/fs/f2fs/namei.c" \
  || fail "F2FS encrypted filename cleanup is missing"
grep -Fq 'ubifs_set_d_ops(dir, dentry);' "$KERNEL_DIR/fs/ubifs/dir.c" \
  || fail "UBIFS encrypted dentry operations are missing"
grep -Fq 'get_page(oldpage);' "$KERNEL_DIR/fs/fuse/dev.c" \
  || fail "FUSE page lifetime fix is missing"
grep -Fq 'rcu_assign_pointer(dev_queue->qdisc, qdisc_default);' "$KERNEL_DIR/net/sched/sch_generic.c" \
  || fail "qdisc deactivation pointer update is missing"

info "Applying the reviewed fixes that were not already present"
python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])

def replace_once(rel, old, new, label):
    path = root / rel
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"applied={label}")

# DWC3: adapt the upstream isochronous continuation fix to Qualcomm's vendor
# stop-transfer signature.  Do not replace the vendor probe/remove teardown.
replace_once(
    "drivers/usb/dwc3/gadget.c",
    "\tif (stop)\n"
    "\t\tdwc3_stop_active_transfer(dwc, dep->number, true);\n"
    "\t/*\n",
    "\tif (stop)\n"
    "\t\tdwc3_stop_active_transfer(dwc, dep->number, true);\n"
    "\telse if (dwc3_gadget_ep_should_continue(dep))\n"
    "\t\t__dwc3_gadget_kick_transfer(dep);\n"
    "\n"
    "\t/*\n",
    "DWC3 endpoint transfer continuation",
)

# exec_mmap: preserve Samsung KDP handling while applying the stable lazy-TLB
# ordering and IRQ exclusion fix around active_mm/mm replacement.
replace_once(
    "fs/exec.c",
    "\ttask_lock(tsk);\n"
    "\tactive_mm = tsk->active_mm;\n"
    "\ttsk->mm = mm;\n"
    "\ttsk->active_mm = mm;\n"
    "\tactivate_mm(active_mm, mm);\n",
    "\ttask_lock(tsk);\n"
    "\n"
    "\tlocal_irq_disable();\n"
    "\tactive_mm = tsk->active_mm;\n"
    "\ttsk->active_mm = mm;\n"
    "\ttsk->mm = mm;\n"
    "\t/*\n"
    "\t * This prevents preemption while active_mm is being loaded and\n"
    "\t * it and mm are being updated, which could cause problems for\n"
    "\t * lazy tlb mm refcounting when these are updated by context\n"
    "\t * switches. Not all architectures can handle irqs off over\n"
    "\t * activate_mm yet.\n"
    "\t */\n"
    "\tif (!IS_ENABLED(CONFIG_ARCH_WANT_IRQS_OFF_ACTIVATE_MM))\n"
    "\t\tlocal_irq_enable();\n"
    "\tactivate_mm(active_mm, mm);\n"
    "\tif (IS_ENABLED(CONFIG_ARCH_WANT_IRQS_OFF_ACTIVATE_MM))\n"
    "\t\tlocal_irq_enable();\n",
    "exec_mmap lazy-TLB ordering",
)

# Keep Samsung's mode-pointer handling and checked integer parser, then add the
# upstream bounds check for reboot=smpN / reboot=sN.
replace_once(
    "kernel/reboot.c",
    "\t\t\t} else\n"
    "\t\t\t\t*mode = REBOOT_SOFT;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tcase 'g':\n",
    "\t\t\t} else\n"
    "\t\t\t\t*mode = REBOOT_SOFT;\n"
    "\n"
    "\t\t\tif (reboot_cpu >= num_possible_cpus()) {\n"
    "\t\t\t\tpr_err(\"Ignoring the CPU number in reboot= option. \"\n"
    "\t\t\t\t       \"CPU %d exceeds possible cpu number %d\\n\",\n"
    "\t\t\t\t       reboot_cpu, num_possible_cpus());\n"
    "\t\t\t\treboot_cpu = 0;\n"
    "\t\t\t}\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tcase 'g':\n",
    "reboot CPU bounds validation",
)

replace_once(
    "scripts/setlocalversion",
    "\t   head=`git rev-parse --verify --short HEAD 2>/dev/null`; then\n",
    "\t   head=$(git rev-parse --verify HEAD 2>/dev/null); then\n",
    "setlocalversion full commit id",
)
PY

# The DWC3 core reject targets the generic upstream teardown.  touchGrass uses
# a Qualcomm-specific probe/remove lifecycle and already performs PHY cleanup
# in its vendor paths, so replacing it with the generic block would be unsafe.
grep -Fq 'dwc3_gadget_exit(dwc);' "$KERNEL_DIR/drivers/usb/dwc3/core.c" \
  || fail "Vendor DWC3 gadget teardown is missing"
grep -Fq 'pm_runtime_allow(&pdev->dev);' "$KERNEL_DIR/drivers/usb/dwc3/core.c" \
  || fail "Vendor DWC3 runtime-PM teardown is missing"

find "$KERNEL_DIR" -type f -name '*.rej' -delete
if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Reject files remain after Linux $TARGET_VERSION review"
fi

grep -Fq 'else if (dwc3_gadget_ep_should_continue(dep))' "$KERNEL_DIR/drivers/usb/dwc3/gadget.c" \
  || fail "DWC3 continuation fix was not applied"
grep -Fq 'CONFIG_ARCH_WANT_IRQS_OFF_ACTIVATE_MM' "$KERNEL_DIR/fs/exec.c" \
  || fail "exec_mmap IRQ ordering fix was not applied"
grep -Fq 'CPU %d exceeds possible cpu number %d' "$KERNEL_DIR/kernel/reboot.c" \
  || fail "reboot CPU validation was not applied"
grep -Fq 'head=$(git rev-parse --verify HEAD 2>/dev/null); then' "$KERNEL_DIR/scripts/setlocalversion" \
  || fail "setlocalversion full commit id fix was not applied"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Resolved tree no longer reports Linux $TARGET_VERSION"
git -C "$KERNEL_DIR" diff --check

{
  echo 'target=4.19.159'
  echo 'rejects_reviewed=20'
  echo 'already_present_or_vendor_newer=16'
  echo 'ported_dwc3_gadget_continuation=yes'
  echo 'ported_exec_mmap_irq_ordering=yes'
  echo 'ported_reboot_cpu_validation=yes'
  echo 'ported_setlocalversion_full_sha=yes'
  echo 'retained_vendor_dwc3_core_lifecycle=yes'
  echo 'remaining_rejects=0'
  echo 'result=reviewed-checkpoint-ready-to-build'
  echo 'flashable=no'
} | tee "$REPORT"
