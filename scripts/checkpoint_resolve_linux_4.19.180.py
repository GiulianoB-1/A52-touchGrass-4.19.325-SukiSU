#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()


def replace_once(rel, old, new, label):
    path = root / rel
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {rel}, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"applied={label}")


def auto_apply(rel):
    reject = root / (rel + ".rej")
    lines = reject.read_text().splitlines()
    patch_text = f"--- a/{rel}\n+++ b/{rel}\n" + "\n".join(lines[1:]) + "\n"
    result = subprocess.run(
        [
            "patch",
            "--batch",
            "--forward",
            "--no-backup-if-mismatch",
            "--fuzz=3",
            "-p1",
        ],
        cwd=root,
        input=patch_text.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout.decode(errors="replace"), end="")
    if result.returncode != 0:
        raise SystemExit(f"auto patch failed: {rel}")
    reject.unlink()


for rel in [
    "arch/arm64/kernel/cpufeature.c",
    "arch/mips/vdso/Makefile",
    "block/genhd.c",
    "drivers/block/zram/zram_drv.c",
    "drivers/md/dm-verity-fec.c",
    "drivers/mmc/core/queue.c",
    "drivers/scsi/sd.c",
    "drivers/usb/gadget/function/f_uac2.c",
    "fs/ext4/namei.c",
    "fs/proc/internal.h",
    "include/asm-generic/vmlinux.lds.h",
    "include/linux/device-mapper.h",
    "include/linux/ipv6.h",
    "mm/memblock.c",
    "mm/page_io.c",
    "net/sunrpc/auth_gss/gss_krb5_mech.c",
]:
    auto_apply(rel)

# Preserve touchGrass BFQ/no-scheduler policy while adopting the upstream
# pre-registration locking fix.
replace_once(
    "block/elevator.c",
    """\t/*
\t * q->sysfs_lock must be held to provide mutual exclusion between
\t * elevator_switch() and here.
\t */
\tmutex_lock(&q->sysfs_lock);
\tif (unlikely(q->elevator))
\t\tgoto out_unlock;
\tif (IS_ENABLED(CONFIG_IOSCHED_BFQ)) {
\t\te = elevator_get(q, \"bfq\", false);
\t\tif (!e)
\t\t\tgoto out_unlock;
\t} else {
\t\te = elevator_get(q, \"mq-deadline\", false);
\t\tif (!e)
\t\t\tgoto out_unlock;
\t}
\terr = blk_mq_init_sched(q, e);
\tif (err)
\t\televator_put(e);
out_unlock:
\tmutex_unlock(&q->sysfs_lock);
\treturn err;
""",
    """\tWARN_ON_ONCE(test_bit(QUEUE_FLAG_REGISTERED, &q->queue_flags));

\tif (unlikely(q->elevator))
\t\tgoto out;
\tif (IS_ENABLED(CONFIG_IOSCHED_BFQ)) {
\t\te = elevator_get(q, \"bfq\", false);
\t\tif (!e)
\t\t\tgoto out;
\t} else {
\t\te = elevator_get(q, \"mq-deadline\", false);
\t\tif (!e)
\t\t\tgoto out;
\t}
\terr = blk_mq_init_sched(q, e);
\tif (err)
\t\televator_put(e);
out:
\treturn err;
""",
    "elevator pre-registration scheduler init",
)

replace_once(
    "drivers/gpu/drm/virtio/virtgpu_vq.c",
    """\tents = kmalloc_array(nents, sizeof(struct virtio_gpu_mem_entry),
\t\t\t     GFP_KERNEL);
""",
    """\tents = kvmalloc_array(nents, sizeof(struct virtio_gpu_mem_entry),
\t\t\t      GFP_KERNEL);
""",
    "virtgpu non-contiguous entry allocation",
)

# Device-mapper restriction aggregation was converted from old all-device
# helpers to the new any-device callback framework. Preserve the vendor crypto
# mode calculation that follows integrity verification.
replace_once(
    "drivers/md/dm-table.c",
    "\tif (dm_table_supports_dax_write_cache(t))\n",
    "\tif (dm_table_any_dev_attr(t, device_dax_write_cache_enabled, NULL))\n",
    "dm dax write-cache aggregation",
)
replace_once(
    "drivers/md/dm-table.c",
    """\t/* Ensure that all underlying devices are non-rotational. */
\tif (dm_table_all_devices_attribute(t, device_is_nonrot))
\t\tblk_queue_flag_set(QUEUE_FLAG_NONROT, q);
\telse
\t\tblk_queue_flag_clear(QUEUE_FLAG_NONROT, q);
""",
    """\t/* Ensure that all underlying devices are non-rotational. */
\tif (dm_table_any_dev_attr(t, device_is_rotational, NULL))
\t\tblk_queue_flag_clear(QUEUE_FLAG_NONROT, q);
\telse
\t\tblk_queue_flag_set(QUEUE_FLAG_NONROT, q);
""",
    "dm rotational aggregation",
)
replace_once(
    "drivers/md/dm-table.c",
    """\tif (dm_table_all_devices_attribute(t, queue_supports_sg_merge))
\t\tblk_queue_flag_clear(QUEUE_FLAG_NO_SG_MERGE, q);
\telse
\t\tblk_queue_flag_set(QUEUE_FLAG_NO_SG_MERGE, q);
""",
    """\tif (dm_table_any_dev_attr(t, queue_no_sg_merge, NULL))
\t\tblk_queue_flag_set(QUEUE_FLAG_NO_SG_MERGE, q);
\telse
\t\tblk_queue_flag_clear(QUEUE_FLAG_NO_SG_MERGE, q);
""",
    "dm sg-merge aggregation",
)
replace_once(
    "drivers/md/dm-table.c",
    """\t/*
\t * Some devices don't use blk_integrity but still want stable pages
\t * because they do their own checksumming.
\t */
\tif (dm_table_requires_stable_pages(t))
""",
    """\t/*
\t * Some devices don't use blk_integrity but still want stable pages
\t * because they do their own checksumming.
\t * If any underlying device requires stable pages, a table must require
\t * them as well. Only targets that support iterate_devices are considered.
\t */
\tif (dm_table_any_dev_attr(t, device_requires_stable_pages, NULL))
""",
    "dm stable-pages aggregation",
)
replace_once(
    "drivers/md/dm-table.c",
    """\tif (blk_queue_add_random(q) && dm_table_all_devices_attribute(t, device_is_not_random))
\t\tblk_queue_flag_clear(QUEUE_FLAG_ADD_RANDOM, q);
""",
    """\tif (blk_queue_add_random(q) &&
\t    dm_table_any_dev_attr(t, device_is_not_random, NULL))
\t\tblk_queue_flag_clear(QUEUE_FLAG_ADD_RANDOM, q);
""",
    "dm entropy aggregation",
)

replace_once(
    "drivers/md/dm-verity-target.c",
    """\t/* SEC: Do not verify RAHEAD bio if status is not OK */
\tif (bio->bi_status &&
\t\t(!verity_fec_is_enabled(io->v) || (bio->bi_opf & REQ_RAHEAD))) {
""",
    """\t/* SEC: Do not verify RAHEAD bio if status is not OK. */
\tif (bio->bi_status &&
\t    (!verity_fec_is_enabled(io->v) ||
\t     verity_is_system_shutting_down() ||
\t     (bio->bi_opf & REQ_RAHEAD))) {
""",
    "dm-verity shutdown and readahead error handling",
)

replace_once(
    "drivers/regulator/core.c",
    """\t/* Recursively resolve the supply of the supply */
\tret = regulator_resolve_supply(r);
\tif (ret < 0) {
\t\tput_device(&r->dev);
\t\treturn ret;
\t}

\tret = set_supply(rdev, r);
\tif (ret < 0) {
\t\tput_device(&r->dev);
\t\treturn ret;
\t}

\treturn 0;
""",
    """\t/* Recursively resolve the supply of the supply */
\tret = regulator_resolve_supply(r);
\tif (ret < 0) {
\t\tput_device(&r->dev);
\t\tgoto out;
\t}

\t/*
\t * Recheck rdev->supply with rdev->mutex held to avoid a race between
\t * the fast-path null check and set_supply() in concurrent tasks.
\t */
\tregulator_lock(rdev);
\tif (rdev->supply) {
\t\tregulator_unlock(rdev);
\t\tput_device(&r->dev);
\t\tgoto out;
\t}

\tret = set_supply(rdev, r);
\tif (ret < 0) {
\t\tregulator_unlock(rdev);
\t\tput_device(&r->dev);
\t\tgoto out;
\t}
\tregulator_unlock(rdev);

\t/* Propagate an already active regulator to its newly resolved supply. */
\tif (rdev->use_count) {
\t\tret = regulator_enable(rdev->supply);
\t\tif (ret < 0) {
\t\t\t_regulator_put(rdev->supply);
\t\t\trdev->supply = NULL;
\t\t\tgoto out;
\t\t}
\t}

out:
\treturn ret;
""",
    "regulator supply resolution race fix",
)

replace_once(
    "fs/ext4/super.c",
    """static void ext4_handle_error(struct super_block *sb, char *buf)
{
\tif (test_opt(sb, WARN_ON_ERROR))
\t\tWARN_ON_ONCE(1);

\tif (sb_rdonly(sb) || ignore_fs_panic)
\t\treturn;

\tif (!test_opt(sb, ERRORS_CONT)) {
\t\tjournal_t *journal = EXT4_SB(sb)->s_journal;

\t\tEXT4_SB(sb)->s_mount_flags |= EXT4_MF_FS_ABORTED;
\t\tif (journal)
\t\t\tjbd2_journal_abort(journal, -EIO);
\t}
""",
    """static void ext4_handle_error(struct super_block *sb, char *buf)
{
\tjournal_t *journal = EXT4_SB(sb)->s_journal;

\tif (test_opt(sb, WARN_ON_ERROR))
\t\tWARN_ON_ONCE(1);

\tif (sb_rdonly(sb) || ignore_fs_panic || test_opt(sb, ERRORS_CONT))
\t\treturn;

\tEXT4_SB(sb)->s_mount_flags |= EXT4_MF_FS_ABORTED;
\tif (journal)
\t\tjbd2_journal_abort(journal, -EIO);
""",
    "ext4 continuous-error handling",
)

replace_once(
    "kernel/exit.c",
    """\tif (unlikely(tsk->flags & PF_EXITING)) {
\t\tpr_alert(\"Fixing recursive fault but reboot is needed!\\n\");
\t\t/*
\t\t * We can do this unlocked here. The futex code uses
\t\t * this flag just to verify whether the pi state
\t\t * cleanup has been done or not. In the worst case it
\t\t * loops once more. We pretend that the cleanup was
\t\t * done as there is no way to return. Either the
\t\t * OWNER_DIED bit is set by now or we push the blocked
\t\t * task into the wait for ever nirwana as well.
\t\t */
\t\ttsk->flags |= PF_EXITPIDONE;
\t\tset_current_state(TASK_UNINTERRUPTIBLE);
\t\tschedule();
\t}

\texit_signals(tsk);  /* sets PF_EXITING */
\tsched_exit(tsk);
\t/*
\t * Ensure that all new tsk->pi_lock acquisitions must observe
\t * PF_EXITING. Serializes against futex.c:attach_to_pi_owner().
\t */
\tsmp_mb();
\t/*
\t * Ensure that we must observe the pi_state in exit_mm() ->
\t * mm_release() -> exit_pi_state_list().
\t */
\traw_spin_lock_irq(&tsk->pi_lock);
\traw_spin_unlock_irq(&tsk->pi_lock);
""",
    """\tif (unlikely(tsk->flags & PF_EXITING)) {
\t\tpr_alert(\"Fixing recursive fault but reboot is needed!\\n\");
\t\tfutex_exit_recursive(tsk);
\t\tset_current_state(TASK_UNINTERRUPTIBLE);
\t\tschedule();
\t}

\texit_signals(tsk);  /* sets PF_EXITING */
\tsched_exit(tsk);
""",
    "futex recursive-exit state handling",
)

# Qualcomm's QRTR implementation already routes the destination port directly
# from to->sq_port, so the upstream QRTR_PORT_CTRL correction is already
# semantically present even though the old generic hunk cannot match.
qrtr = (root / "net/qrtr/qrtr.c").read_text()
if "hdr->dst_port_id = cpu_to_le32(to->sq_port);" not in qrtr:
    raise SystemExit("QRTR destination-port semantics are not present")

# Rejects whose final result was already present after clean hunks or in
# vendor-adapted code. Validate representative postconditions before removal.
checks = {
    "drivers/hid/hid-core.c": [
        "static struct hid_field *hid_register_field(struct hid_report *report, unsigned usages)",
        "usages * sizeof(unsigned)), GFP_KERNEL);",
    ],
    "drivers/usb/gadget/composite.c": [
        "spin_unlock_irqrestore(&cdev->lock, flags);",
        "status = usb_gadget_deactivate(cdev->gadget);",
        "status = usb_gadget_activate(cdev->gadget);",
    ],
    "drivers/usb/gadget/configfs.c": [
        "list_for_each_entry_safe_reverse(f, tmp, &c->functions, list)",
        ".max_speed\t= USB_SPEED_SUPER_PLUS,",
    ],
    "fs/ext4/inode.c": ["inode->i_state &= ~I_DIRTY_TIME;"],
    "fs/fs-writeback.c": [
        "mark_inode_dirty_sync(inode);",
        "moved = move_expired_inodes(&wb->b_dirty, &wb->b_io, dirtied_before);",
    ],
    "fs/quota/quota_tree.c": ["(loff_t)blk << info->dqi_blocksize_bits"],
    "fs/xfs/xfs_trans_inode.c": ["inode->i_state &= ~I_DIRTY_TIME;"],
    "include/linux/fs.h": ["#define I_DIRTY_TIME\t\t(1 << 11)"],
    "include/trace/events/writeback.h": ["{I_DIRTY_TIME,\t\t\"I_DIRTY_TIME\"}"],
    "kernel/trace/ring_buffer.c": [
        "mutex_lock(&buffer->mutex);",
        "mutex_unlock(&buffer->mutex);",
    ],
    "net/qrtr/qrtr.c": ["hdr->dst_port_id = cpu_to_le32(to->sq_port);"],
}
for rel, required in checks.items():
    text = (root / rel).read_text()
    for item in required:
        if item not in text:
            raise SystemExit(f"postcondition missing in {rel}: {item!r}")
    reject = root / (rel + ".rej")
    if reject.exists():
        reject.unlink()

negative_checks = {
    "include/linux/fs.h": ["I_DIRTY_TIME_EXPIRED"],
    "include/trace/events/writeback.h": ["I_DIRTY_TIME_EXPIRED"],
    "fs/fs-writeback.c": ["I_DIRTY_TIME_EXPIRED"],
}
for rel, forbidden in negative_checks.items():
    text = (root / rel).read_text()
    for item in forbidden:
        if item in text:
            raise SystemExit(f"forbidden legacy code remains in {rel}: {item}")

gss_text = (root / "net/sunrpc/auth_gss/gss_krb5_mech.c").read_text()
if (
    "simple_get_bytes(const void *p" in gss_text
    or "simple_get_netobj(const void *p" in gss_text
):
    raise SystemExit("duplicate local GSS parsing helpers remain")

for rel in [
    "block/elevator.c",
    "drivers/gpu/drm/virtio/virtgpu_vq.c",
    "drivers/md/dm-table.c",
    "drivers/md/dm-verity-target.c",
    "drivers/regulator/core.c",
    "fs/ext4/super.c",
    "kernel/exit.c",
]:
    reject = root / (rel + ".rej")
    if reject.exists():
        reject.unlink()

remaining = sorted(str(path.relative_to(root)) for path in root.rglob("*.rej"))
if remaining:
    raise SystemExit("remaining rejects: " + ", ".join(remaining))

required_global = {
    "arch/arm64/kernel/cpufeature.c": [
        "MIDR_ALL_VERSIONS(MIDR_CORTEX_A55)",
        "MIDR_RANGE(MIDR_KRYO5S, 13, 14, 13, 14)",
    ],
    "block/elevator.c": [
        "WARN_ON_ONCE(test_bit(QUEUE_FLAG_REGISTERED, &q->queue_flags))",
        "elevator_get(q, \"bfq\", false)",
    ],
    "drivers/md/dm-verity-fec.c": ["u64 hash_blocks, fec_blocks;"],
    "drivers/md/dm-verity-target.c": [
        "verity_is_system_shutting_down()",
        "(bio->bi_opf & REQ_RAHEAD)",
    ],
    "drivers/regulator/core.c": ["regulator_lock(rdev);", "if (rdev->use_count)"],
    "kernel/exit.c": ["futex_exit_recursive(tsk);", "sched_exit(tsk);"],
    "mm/page_io.c": ["map_swap_page(page, &sis->bdev)"],
}
for rel, required in required_global.items():
    text = (root / rel).read_text()
    for item in required:
        if item not in text:
            raise SystemExit(f"final check missing {item!r} in {rel}")

print("result=reviewed-linux-4.19.180-resolver-complete")
