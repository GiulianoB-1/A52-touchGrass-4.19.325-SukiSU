#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.164
TO_TAG=v$TARGET_VERSION
REPORT="$ARTIFACTS_DIR/reviewed-resolutions-$TO_TAG.txt"
ACTUAL="$ARTIFACTS_DIR/actual-rejects-$TO_TAG.txt"
EXPECTED="$ARTIFACTS_DIR/expected-rejects-$TO_TAG.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION after checkpoint apply"

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ACTUAL"
cat > "$EXPECTED" <<'EOF'
arch/arm64/include/asm/lse.h.rej
arch/arm64/include/asm/pgtable.h.rej
drivers/hid/hid-ids.h.rej
drivers/hid/hid-quirks.c.rej
drivers/mmc/core/block.c.rej
drivers/net/ethernet/qualcomm/rmnet/rmnet_handlers.c.rej
drivers/scsi/ufs/ufshcd.c.rej
drivers/soc/qcom/smp2p.c.rej
drivers/tty/tty_io.c.rej
drivers/tty/tty_jobctrl.c.rej
drivers/usb/gadget/function/f_acm.c.rej
drivers/usb/gadget/function/f_fs.c.rej
drivers/usb/gadget/function/f_midi.c.rej
fs/quota/quota_v2.c.rej
include/linux/tty.h.rej
sound/soc/codecs/wm_adsp.c.rej
EOF
diff -u "$EXPECTED" "$ACTUAL" > "$ARTIFACTS_DIR/reject-set-$TO_TAG.diff" \
  || fail "Linux $TARGET_VERSION reject set changed; review is required"

info "Validating Linux 4.19.164 changes already carried or cleanly applied"
# LSE keeps the touchGrass Clang-LTO conditional preamble; the generic top-level
# definition added cleanly by stable is removed below to avoid a macro redefinition.
grep -Fq '#ifdef CONFIG_LTO_CLANG' "$KERNEL_DIR/arch/arm64/include/asm/lse.h" \
  || fail "touchGrass LSE Clang-LTO handling is missing"
grep -Fq 'ALTERNATIVE(llsc, __LSE_PREAMBLE lse, ARM64_HAS_LSE_ATOMICS)' \
  "$KERNEL_DIR/arch/arm64/include/asm/lse.h" \
  || fail "LSE runtime preamble use is missing"

# The PTE accessibility and hardware-dirty write-protect fixes applied cleanly
# around Samsung RKP/strict-BBM additions.
if grep -Fq 'pte_valid_young' "$KERNEL_DIR/arch/arm64/include/asm/pgtable.h"; then
  fail "obsolete pte_valid_young helper remains"
fi
grep -Fq '(mm_tlb_flush_pending(mm) ? pte_present(pte) : pte_valid(pte))' \
  "$KERNEL_DIR/arch/arm64/include/asm/pgtable.h" \
  || fail "arm64 pte_accessible fix is missing"
grep -Fq 'if (pte_hw_dirty(pte))' "$KERNEL_DIR/arch/arm64/include/asm/pgtable.h" \
  || fail "arm64 hardware-dirty write-protect fix is missing"

grep -Fq 'USB_VENDOR_ID_GAMEVICE' "$KERNEL_DIR/drivers/hid/hid-ids.h" \
  || fail "Gamevice HID IDs are missing"
grep -Fq 'USB_DEVICE_ID_GAMEVICE_KISHI' "$KERNEL_DIR/drivers/hid/hid-ids.h" \
  || fail "Gamevice Kishi HID ID is missing"
grep -Fq 'HID_QUIRK_INCREMENT_USAGE_ON_DUPLICATE' "$KERNEL_DIR/drivers/hid/hid-quirks.c" \
  || fail "Gamevice duplicate-usage HID quirk is missing"

# touchGrass already uses the response BUSY bit directly, which covers R1B and
# avoids the original partial-mask bug fixed upstream.
grep -Fq 'if (idata->rpmb || (cmd.flags & MMC_RSP_BUSY)) {' \
  "$KERNEL_DIR/drivers/mmc/core/block.c" \
  || fail "MMC busy-response completion check is missing"

# Two UFS hunks applied cleanly despite the rejected combined patch.
grep -Fq 'if (queue_work(hba->clk_gating.clk_gating_workq,' \
  "$KERNEL_DIR/drivers/scsi/ufs/ufshcd.c" \
  || fail "UFS queued ungate request fix is missing"
grep -Fq 'pm_runtime_get_sync(hba->dev);' "$KERNEL_DIR/drivers/scsi/ufs/ufshcd.c" \
  || fail "UFS shutdown runtime-resume fix is missing"

# TTY locking fixes applied cleanly around Samsung changes.
grep -Fq 'session = get_pid(tty->session);' "$KERNEL_DIR/drivers/tty/tty_io.c" \
  || fail "TTY SAK session reference fix is missing"
grep -Fq 'put_pid(session);' "$KERNEL_DIR/drivers/tty/tty_io.c" \
  || fail "TTY SAK session release is missing"
grep -Fq 'spin_lock_irq(&real_tty->ctrl_lock);' "$KERNEL_DIR/drivers/tty/tty_jobctrl.c" \
  || fail "TTY process-group ctrl_lock fix is missing"
grep -Fq 'tty_lock(tty);' "$KERNEL_DIR/drivers/tty/tty_jobctrl.c" \
  || fail "TTY disassociation serialization is missing"
grep -Fq 'Writes protected by both ctrl lock and legacy mutex' "$KERNEL_DIR/include/linux/tty.h" \
  || fail "TTY session locking annotation is missing"

# USB FunctionFS/ACM and quota changes applied cleanly.
grep -Fq 'acm_ss_function, acm_ss_function);' "$KERNEL_DIR/drivers/usb/gadget/function/f_acm.c" \
  || fail "ACM SuperSpeedPlus descriptors are missing"
grep -Fq 'case USB_SPEED_SUPER_PLUS:' "$KERNEL_DIR/drivers/usb/gadget/function/f_fs.c" \
  || fail "FunctionFS SuperSpeedPlus endpoint handling is missing"
grep -Fq 'struct usb_endpoint_descriptor desc1, *desc;' "$KERNEL_DIR/drivers/usb/gadget/function/f_fs.c" \
  || fail "FunctionFS endpoint descriptor copy fix is missing"
grep -Fq 'func->function.ssp_descriptors = func->function.ss_descriptors;' \
  "$KERNEL_DIR/drivers/usb/gadget/function/f_fs.c" \
  || fail "FunctionFS SSP descriptor assignment is missing"
grep -Fq 'ret = -EUCLEAN;' "$KERNEL_DIR/fs/quota/quota_v2.c" \
  || fail "quota header validation is missing"
grep -Fq 'Free block number too big' "$KERNEL_DIR/fs/quota/quota_v2.c" \
  || fail "quota free-block bounds validation is missing"

info "Applying the reviewed Linux 4.19.164 adaptations"
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

# Preserve the touchGrass CONFIG_LTO_CLANG split.  Stable added the same macro
# globally before the includes, which otherwise leaves a duplicate definition.
replace_once(
    "arch/arm64/include/asm/lse.h",
    "#if defined(CONFIG_AS_LSE) && defined(CONFIG_ARM64_LSE_ATOMICS)\n\n"
    "#define __LSE_PREAMBLE\t\".arch_extension lse\\n\"\n\n"
    "#include <linux/compiler_types.h>\n",
    "#if defined(CONFIG_AS_LSE) && defined(CONFIG_ARM64_LSE_ATOMICS)\n\n"
    "#include <linux/compiler_types.h>\n",
    "retain touchGrass LSE LTO preamble policy",
)

# Add the only HID quirk not already present in the vendor table.
replace_once(
    "drivers/hid/hid-quirks.c",
    "\t{ HID_USB_DEVICE(USB_VENDOR_ID_GREENASIA, USB_DEVICE_ID_GREENASIA_DUAL_USB_JOYPAD), HID_QUIRK_MULTI_INPUT },\n",
    "\t{ HID_USB_DEVICE(USB_VENDOR_ID_GREENASIA, USB_DEVICE_ID_GREENASIA_DUAL_SAT_ADAPTOR), HID_QUIRK_MULTI_INPUT },\n"
    "\t{ HID_USB_DEVICE(USB_VENDOR_ID_GREENASIA, USB_DEVICE_ID_GREENASIA_DUAL_USB_JOYPAD), HID_QUIRK_MULTI_INPUT },\n",
    "GreenAsia dual SAT multi-input quirk",
)

# Adapt the upstream missing-port guard to the Qualcomm rmnet_get_port() API.
replace_once(
    "drivers/net/ethernet/qualcomm/rmnet/rmnet_handlers.c",
    "\tdev = skb->dev;\n"
    "\tport = rmnet_get_port(dev);\n\n"
    "\tswitch (port->rmnet_mode) {\n",
    "\tdev = skb->dev;\n"
    "\tport = rmnet_get_port(dev);\n"
    "\tif (unlikely(!port)) {\n"
    "\t\tatomic_long_inc(&dev->rx_nohandler);\n"
    "\t\tkfree_skb(skb);\n"
    "\t\tgoto done;\n"
    "\t}\n\n"
    "\tswitch (port->rmnet_mode) {\n",
    "rmnet missing-port receive guard",
)

# Apply the UFS devfreq runtime-active guard; the clock-gating and shutdown
# hunks already applied cleanly to the larger Samsung UFS implementation.
replace_once(
    "drivers/scsi/ufs/ufshcd.c",
    "\tstart = ktime_get();\n"
    "\tret = ufshcd_devfreq_scale(hba, scale_up);\n"
    "\ttrace_ufshcd_profile_clk_scaling(dev_name(hba->dev),\n",
    "\tpm_runtime_get_noresume(hba->dev);\n"
    "\tif (!pm_runtime_active(hba->dev)) {\n"
    "\t\tpm_runtime_put_noidle(hba->dev);\n"
    "\t\tret = -EAGAIN;\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\tstart = ktime_get();\n"
    "\tret = ufshcd_devfreq_scale(hba, scale_up);\n"
    "\tpm_runtime_put(hba->dev);\n"
    "\ttrace_ufshcd_profile_clk_scaling(dev_name(hba->dev),\n",
    "UFS devfreq runtime-active guard",
)

# smp2p update_bits can be called from IRQ context, so retain the vendor logging
# while converting the entry lock to irqsave/irqrestore.
replace_once(
    "drivers/soc/qcom/smp2p.c",
    "\tstruct smp2p_entry *entry = data;\n"
    "\tu32 orig;\n",
    "\tstruct smp2p_entry *entry = data;\n"
    "\tunsigned long flags;\n"
    "\tu32 orig;\n",
    "SMP2P irq flags declaration",
)
replace_once(
    "drivers/soc/qcom/smp2p.c",
    "\tspin_lock(&entry->lock);\n"
    "\tval = orig = readl(entry->value);\n"
    "\tval &= ~mask;\n"
    "\tval |= value;\n"
    "\twritel(val, entry->value);\n"
    "\tspin_unlock(&entry->lock);\n",
    "\tspin_lock_irqsave(&entry->lock, flags);\n"
    "\tval = orig = readl(entry->value);\n"
    "\tval &= ~mask;\n"
    "\tval |= value;\n"
    "\twritel(val, entry->value);\n"
    "\tspin_unlock_irqrestore(&entry->lock, flags);\n",
    "SMP2P irq-safe entry update",
)

# The first MIDI hunk applied and introduced goto midi_free; add the rejected
# cleanup label without disturbing the vendor allocation layout.
replace_once(
    "drivers/usb/gadget/function/f_midi.c",
    "\tfi->f = &midi->func;\n"
    "\treturn &midi->func;\n\n"
    "setup_fail:\n"
    "\tmutex_unlock(&opts->lock);\n"
    "\tkfree(midi);\n"
    "\treturn ERR_PTR(status);\n",
    "\tfi->f = &midi->func;\n"
    "\treturn &midi->func;\n\n"
    "midi_free:\n"
    "\tif (midi)\n"
    "\t\tkfree(midi->id);\n"
    "\tkfree(midi);\n"
    "setup_fail:\n"
    "\tmutex_unlock(&opts->lock);\n"
    "\treturn ERR_PTR(status);\n",
    "MIDI allocation failure cleanup",
)

# The allocation failure goto applied cleanly; add the missing list removal
# before freeing the ADSP control.
replace_once(
    "sound/soc/codecs/wm_adsp.c",
    "err_ctl_cache:\n"
    "\tkfree(ctl->cache);\n",
    "err_list_del:\n"
    "\tlist_del(&ctl->list);\n"
    "err_ctl_cache:\n"
    "\tkfree(ctl->cache);\n",
    "WM ADSP control list cleanup",
)
PY

find "$KERNEL_DIR" -type f -name '*.rej' -delete
if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Reject files remain after Linux $TARGET_VERSION review"
fi

# Final post-resolution assertions.
test "$(grep -Fc '#define __LSE_PREAMBLE' "$KERNEL_DIR/arch/arm64/include/asm/lse.h")" -eq 2 \
  || fail "unexpected LSE preamble definition count"
grep -Fq 'USB_DEVICE_ID_GREENASIA_DUAL_SAT_ADAPTOR' "$KERNEL_DIR/drivers/hid/hid-quirks.c" \
  || fail "GreenAsia dual SAT quirk was not applied"
grep -Fq 'atomic_long_inc(&dev->rx_nohandler);' \
  "$KERNEL_DIR/drivers/net/ethernet/qualcomm/rmnet/rmnet_handlers.c" \
  || fail "rmnet missing-port guard was not applied"
grep -Fq 'pm_runtime_get_noresume(hba->dev);' "$KERNEL_DIR/drivers/scsi/ufs/ufshcd.c" \
  || fail "UFS devfreq runtime guard was not applied"
grep -Fq 'spin_lock_irqsave(&entry->lock, flags);' "$KERNEL_DIR/drivers/soc/qcom/smp2p.c" \
  || fail "SMP2P irq-safe locking was not applied"
grep -Fq 'midi_free:' "$KERNEL_DIR/drivers/usb/gadget/function/f_midi.c" \
  || fail "MIDI cleanup label was not applied"
grep -Fq 'err_list_del:' "$KERNEL_DIR/sound/soc/codecs/wm_adsp.c" \
  || fail "WM ADSP list cleanup label was not applied"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Resolved tree no longer reports Linux $TARGET_VERSION"
git -C "$KERNEL_DIR" diff --check

{
  echo 'target=4.19.164'
  echo 'rejects_reviewed=16'
  echo 'already_present_or_cleanly_applied=9'
  echo 'ported_or_adapted=7'
  echo 'retained_vendor_mmc_busy_semantics=yes'
  echo 'retained_vendor_lse_lto_policy=yes'
  echo 'remaining_rejects=0'
  echo 'result=reviewed-checkpoint-ready-to-build'
  echo 'flashable=no'
} | tee "$REPORT"
