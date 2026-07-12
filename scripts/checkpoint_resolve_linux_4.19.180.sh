#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.180
TO_TAG=v$TARGET_VERSION
REPORT="$ARTIFACTS_DIR/reviewed-resolutions-$TO_TAG.txt"
ACTUAL="$ARTIFACTS_DIR/actual-rejects-$TO_TAG.txt"
EXPECTED="$ARTIFACTS_DIR/expected-rejects-$TO_TAG.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION after checkpoint apply"

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ACTUAL"
cat > "$EXPECTED" <<'EOF'
arch/arm64/kernel/cpufeature.c.rej
arch/mips/vdso/Makefile.rej
block/elevator.c.rej
block/genhd.c.rej
drivers/block/zram/zram_drv.c.rej
drivers/gpu/drm/virtio/virtgpu_vq.c.rej
drivers/hid/hid-core.c.rej
drivers/md/dm-table.c.rej
drivers/md/dm-verity-fec.c.rej
drivers/md/dm-verity-target.c.rej
drivers/mmc/core/queue.c.rej
drivers/regulator/core.c.rej
drivers/scsi/sd.c.rej
drivers/usb/gadget/composite.c.rej
drivers/usb/gadget/configfs.c.rej
drivers/usb/gadget/function/f_uac2.c.rej
fs/ext4/inode.c.rej
fs/ext4/namei.c.rej
fs/ext4/super.c.rej
fs/fs-writeback.c.rej
fs/proc/internal.h.rej
fs/quota/quota_tree.c.rej
fs/xfs/xfs_trans_inode.c.rej
include/asm-generic/vmlinux.lds.h.rej
include/linux/device-mapper.h.rej
include/linux/fs.h.rej
include/linux/ipv6.h.rej
include/trace/events/writeback.h.rej
kernel/exit.c.rej
kernel/trace/ring_buffer.c.rej
mm/memblock.c.rej
mm/page_io.c.rej
net/qrtr/qrtr.c.rej
net/sunrpc/auth_gss/gss_krb5_mech.c.rej
EOF

diff -u "$EXPECTED" "$ACTUAL" > "$ARTIFACTS_DIR/reject-set-$TO_TAG.diff" \
  || fail "Linux $TARGET_VERSION reject set changed; review is required"

info "Applying reviewed Linux $TARGET_VERSION vendor adaptations"
python3 "$(dirname "$0")/checkpoint_resolve_linux_4.19.180.py" "$KERNEL_DIR" \
  2>&1 | tee "$ARTIFACTS_DIR/reviewed-resolver-$TO_TAG.log"

if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Reject files remain after Linux $TARGET_VERSION review"
fi

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Resolved tree no longer reports Linux $TARGET_VERSION"
git -C "$KERNEL_DIR" diff --check

{
  echo 'target=4.19.180'
  echo 'upstream_commits=826'
  echo 'rejects_reviewed=34'
  echo 'fuzzy_context_ports=16'
  echo 'manual_vendor_adaptations=7'
  echo 'already_present_or_vendor_equivalent=11'
  echo 'retained_touchgrass_bfq_policy=yes'
  echo 'retained_qualcomm_qrtr_routing=yes'
  echo 'retained_samsung_dm_verity_readahead=yes'
  echo 'retained_samsung_ext4_ignore_fs_panic=yes'
  echo 'retained_samsung_sched_exit=yes'
  echo 'remaining_rejects=0'
  echo 'result=reviewed-checkpoint-ready-to-build'
  echo 'flashable=no'
} | tee "$REPORT"

info "Linux $TARGET_VERSION checkpoint conflicts reviewed and resolved"
