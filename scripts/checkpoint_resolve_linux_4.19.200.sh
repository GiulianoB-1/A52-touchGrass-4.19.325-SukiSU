#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.200
TO_TAG=v$TARGET_VERSION
REPORT="$ARTIFACTS_DIR/reviewed-resolutions-$TO_TAG.txt"
ACTUAL="$ARTIFACTS_DIR/actual-rejects-$TO_TAG.txt"
EXPECTED="$ARTIFACTS_DIR/expected-rejects-$TO_TAG.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION after checkpoint apply"

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ACTUAL"
cat > "$EXPECTED" <<'EOF'
arch/arm64/include/asm/alternative.h.rej
arch/x86/entry/vdso/vma.c.rej
drivers/hid/hid-ids.h.rej
drivers/mmc/core/bus.c.rej
drivers/mmc/core/core.c.rej
drivers/mmc/core/core.h.rej
drivers/mmc/core/mmc.c.rej
drivers/mmc/core/mmc_ops.c.rej
drivers/mmc/core/sd.c.rej
drivers/thermal/thermal_sysfs.c.rej
drivers/usb/core/hub.c.rej
drivers/usb/dwc3/core.c.rej
drivers/usb/dwc3/debug.h.rej
drivers/usb/dwc3/debugfs.c.rej
drivers/usb/dwc3/gadget.c.rej
drivers/usb/gadget/config.c.rej
drivers/usb/gadget/configfs.c.rej
drivers/usb/gadget/function/f_fs.c.rej
drivers/usb/gadget/function/f_ncm.c.rej
fs/crypto/fname.c.rej
fs/ext4/extents.c.rej
fs/ext4/inode.c.rej
fs/ext4/namei.c.rej
fs/ext4/super.c.rej
fs/seq_file.c.rej
include/asm-generic/vmlinux.lds.h.rej
include/linux/mm.h.rej
include/linux/netdevice.h.rej
include/net/af_unix.h.rej
include/net/sctp/structs.h.rej
include/uapi/linux/bpf.h.rej
kernel/bpf/verifier.c.rej
kernel/cgroup/cgroup.c.rej
kernel/cpu.c.rej
kernel/locking/lockdep.c.rej
kernel/locking/mutex.c.rej
kernel/sched/fair.c.rej
kernel/trace/bpf_trace.c.rej
kernel/workqueue.c.rej
mm/memory.c.rej
mm/rmap.c.rej
net/Makefile.rej
net/core/filter.c.rej
net/ipv4/tcp_ipv4.c.rej
net/ipv4/udp.c.rej
net/ipv4/udp_offload.c.rej
net/ipv6/tcp_ipv6.c.rej
net/mac802154/llsec.c.rej
net/qrtr/qrtr.c.rej
net/sctp/bind_addr.c.rej
net/sctp/input.c.rej
net/sctp/ipv6.c.rej
net/sctp/protocol.c.rej
net/sctp/sm_make_chunk.c.rej
net/unix/Kconfig.rej
net/unix/Makefile.rej
net/unix/af_unix.c.rej
net/unix/garbage.c.rej
net/wireless/util.c.rej
security/selinux/avc.c.rej
sound/usb/card.c.rej
sound/usb/usbaudio.h.rej
EOF

diff -u "$EXPECTED" "$ACTUAL" > "$ARTIFACTS_DIR/reject-set-$TO_TAG.diff" \
  || fail "Linux $TARGET_VERSION reject set changed; review is required"

info "Applying reviewed Linux $TARGET_VERSION vendor adaptations"
python3 "$(dirname "$0")/checkpoint_resolve_linux_4.19.200.py" "$KERNEL_DIR" \
  2>&1 | tee "$ARTIFACTS_DIR/reviewed-resolver-$TO_TAG.log"

info "Repairing Linux 4.19.200 compile API mismatches"
"$(dirname "$0")/checkpoint_fix_linux_4.19.200_compile.sh"

info "Repairing vendor linker-script NOINSTR placement"
"$(dirname "$0")/checkpoint_fix_linux_4.19.200_noinstr.sh"

if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Reject files remain after Linux $TARGET_VERSION review"
fi

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Resolved tree no longer reports Linux $TARGET_VERSION"
git -C "$KERNEL_DIR" diff --check

{
  echo 'target=4.19.200'
  echo 'hardware_tested_base=4.19.180'
  echo 'upstream_commits=1000'
  echo 'rejects_reviewed=62'
  echo 'fuzzy_context_ports=27'
  echo 'manual_vendor_adaptations=12'
  echo 'already_present_or_vendor_equivalent=23'
  echo 'retained_qualcomm_dwc3_lifecycle=yes'
  echo 'retained_qualcomm_qrtr_allocator=yes'
  echo 'retained_samsung_ncm_wrapper=yes'
  echo 'retained_samsung_mmc_cache_disable_quirk=yes'
  echo 'retained_newer_cfg80211_key_policy=yes'
  echo 'ported_functionfs_ownership_fix=yes'
  echo 'ported_arm64_alternative_ordering=yes'
  echo 'repaired_compile_api_mismatches=yes'
  echo 'repaired_noinstr_linker_placement=yes'
  echo 'remaining_rejects=0'
  echo 'result=reviewed-checkpoint-ready-to-build'
  echo 'flashable=no'
} | tee "$REPORT"

info "Linux $TARGET_VERSION checkpoint conflicts reviewed and resolved"
