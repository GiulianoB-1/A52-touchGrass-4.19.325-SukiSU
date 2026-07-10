#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/21_build_ack_6_1_probe.sh"
NEXT="$SCRIPT_DIR/22_build_ack_6_1_probe_without_gunyah.sh"

test -f "$TARGET" || {
  echo "Missing ACK 6.1 probe build script: $TARGET" >&2
  exit 1
}

test -f "$NEXT" || {
  echo "Missing Gunyah compatibility wrapper: $NEXT" >&2
  exit 1
}

python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

start = "cat > \"$SRC_DIR/drivers/misc/a52xq_hybrid_probe.c\" <<'PROBE_C'\n"
end = "\nPROBE_C\n"

if text.count(start) != 1:
    raise SystemExit(f"probe heredoc start: expected one match, found {text.count(start)}")

head, rest = text.split(start, 1)
if end not in rest:
    raise SystemExit("probe heredoc end not found")
_, tail = rest.split(end, 1)

probe = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52XQ ACK 6.1 diagnostic probe v3.
 *
 * This probe writes a guarded, self-describing copy of the printk ring buffer
 * into the previously audited all-zero final 2 MiB of Samsung's 64 MiB
 * logdump partition. It writes before panic, while storage is healthy, and
 * commits the 4 KiB header last. Any unexpected partition geometry or any
 * non-zero byte in the target slot causes a fail-closed refusal to write.
 */
#include <linux/blkdev.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/fcntl.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/panic.h>
#include <linux/string.h>
#include <linux/vmalloc.h>

#define A52XQ_V3_MARKER \
    "A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_REACHED_LATE_INIT"
#define A52XQ_V3_WRITE_OK \
    "A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WRITE_VERIFIED"
#define A52XQ_V3_WRITE_REFUSED \
    "A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WRITE_REFUSED"
#define A52XQ_V3_PANIC \
    "A52XQ_HYBRID_GKI_6_1_V3_INTENTIONAL_PANIC_NO_AUTO_REBOOT"
#define A52XQ_V3_MAGIC "A52XQ_LOGDUMP_V3\n"

#define A52XQ_PARTITION_BYTES 67108864ULL
#define A52XQ_SLOT_OFFSET     65011712LL
#define A52XQ_SLOT_BYTES      2097152U
#define A52XQ_HEADER_BYTES    4096U
#define A52XQ_PAYLOAD_BYTES   (A52XQ_SLOT_BYTES - A52XQ_HEADER_BYTES)

#define A52XQ_ACK_COMMIT \
    "52939c41021c7c0646679b68df13e82c1a5be699"
#define A52XQ_ZERO_SLOT_SHA256 \
    "5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee"

static const char * const a52xq_logdump_paths[] = {
    "/dev/sde18",
    "/dev/block/sde18",
    "/dev/block/by-name/logdump",
};

static u32 a52xq_fnv1a32(const u8 *buf, size_t len)
{
    u32 hash = 2166136261U;
    size_t i;

    for (i = 0; i < len; i++) {
        hash ^= buf[i];
        hash *= 16777619U;
    }

    return hash;
}

static ssize_t a52xq_read_full(struct file *file, void *buf, size_t len,
                               loff_t *pos)
{
    size_t done = 0;

    while (done < len) {
        ssize_t ret = kernel_read(file, (u8 *)buf + done, len - done, pos);

        if (ret < 0)
            return ret;
        if (!ret)
            return -EIO;
        done += ret;
    }

    return done;
}

static ssize_t a52xq_write_full(struct file *file, const void *buf, size_t len,
                                loff_t *pos)
{
    size_t done = 0;

    while (done < len) {
        ssize_t ret = kernel_write(file, (const u8 *)buf + done,
                                   len - done, pos);

        if (ret < 0)
            return ret;
        if (!ret)
            return -EIO;
        done += ret;
    }

    return done;
}

static struct file *a52xq_open_logdump(const char **used_path)
{
    struct file *file;
    int retry;
    size_t i;

    for (retry = 0; retry < 50; retry++) {
        for (i = 0; i < ARRAY_SIZE(a52xq_logdump_paths); i++) {
            file = filp_open(a52xq_logdump_paths[i],
                             O_RDWR | O_DSYNC | O_LARGEFILE, 0);
            if (!IS_ERR(file)) {
                *used_path = a52xq_logdump_paths[i];
                return file;
            }
        }
        msleep(100);
    }

    return ERR_PTR(-ENOENT);
}

static int a52xq_flush_logdump(struct file *file)
{
    struct block_device *bdev = I_BDEV(file_inode(file));
    int ret;

    ret = vfs_fsync(file, 0);
    if (ret)
        return ret;

    return blkdev_issue_flush(bdev);
}

static int a52xq_write_logdump_record(void)
{
    struct kmsg_dump_iter iter = { };
    struct block_device *bdev;
    struct file *file;
    const char *used_path = NULL;
    u8 *record;
    loff_t pos;
    size_t payload_len = 0;
    size_t header_len;
    u32 expected_hash;
    u32 readback_hash;
    int ret = 0;
    size_t i;

    record = vzalloc(A52XQ_SLOT_BYTES);
    if (!record)
        return -ENOMEM;

    file = a52xq_open_logdump(&used_path);
    if (IS_ERR(file)) {
        ret = PTR_ERR(file);
        pr_emerg("A52XQ_V3_LOGDUMP_OPEN_FAILED ret=%d\n", ret);
        goto out_free;
    }

    if (!S_ISBLK(file_inode(file)->i_mode)) {
        ret = -ENOTBLK;
        pr_emerg("A52XQ_V3_LOGDUMP_NOT_BLOCK_DEVICE path=%s\n", used_path);
        goto out_close;
    }

    bdev = I_BDEV(file_inode(file));
    if (bdev_nr_bytes(bdev) != A52XQ_PARTITION_BYTES) {
        ret = -EFBIG;
        pr_emerg("A52XQ_V3_LOGDUMP_SIZE_MISMATCH path=%s bytes=%llu expected=%llu\n",
                 used_path,
                 (unsigned long long)bdev_nr_bytes(bdev),
                 (unsigned long long)A52XQ_PARTITION_BYTES);
        goto out_close;
    }

    pos = A52XQ_SLOT_OFFSET;
    ret = a52xq_read_full(file, record, A52XQ_SLOT_BYTES, &pos);
    if (ret < 0) {
        pr_emerg("A52XQ_V3_LOGDUMP_PREFLIGHT_READ_FAILED ret=%d\n", ret);
        goto out_close;
    }

    for (i = 0; i < A52XQ_SLOT_BYTES; i++) {
        if (record[i]) {
            ret = -EUCLEAN;
            pr_emerg("A52XQ_V3_LOGDUMP_SLOT_NOT_ZERO offset=0x%zx value=0x%02x\n",
                     i, record[i]);
            goto out_close;
        }
    }

    memset(record, 0, A52XQ_SLOT_BYTES);
    kmsg_dump_rewind(&iter);
    if (!kmsg_dump_get_buffer(&iter, true,
                              record + A52XQ_HEADER_BYTES,
                              A52XQ_PAYLOAD_BYTES - 1,
                              &payload_len)) {
        ret = -ENODATA;
        pr_emerg("A52XQ_V3_LOGDUMP_KMSG_CAPTURE_FAILED\n");
        goto out_close;
    }

    record[A52XQ_HEADER_BYTES + payload_len] = '\n';
    payload_len++;
    expected_hash = a52xq_fnv1a32(record + A52XQ_HEADER_BYTES,
                                  payload_len);

    header_len = scnprintf(record, A52XQ_HEADER_BYTES,
        A52XQ_V3_MAGIC
        "format_version=1\n"
        "state=complete\n"
        "ack_commit=%s\n"
        "partition_bytes=%llu\n"
        "slot_offset=%lld\n"
        "slot_bytes=%u\n"
        "header_bytes=%u\n"
        "payload_offset=%u\n"
        "payload_length=%zu\n"
        "payload_fnv1a32=%08x\n"
        "audited_zero_slot_sha256=%s\n"
        "opened_path=%s\n"
        "marker=%s\n"
        "panic_marker=%s\n"
        "END_HEADER\n",
        A52XQ_ACK_COMMIT,
        (unsigned long long)A52XQ_PARTITION_BYTES,
        (long long)A52XQ_SLOT_OFFSET,
        A52XQ_SLOT_BYTES,
        A52XQ_HEADER_BYTES,
        A52XQ_HEADER_BYTES,
        payload_len,
        expected_hash,
        A52XQ_ZERO_SLOT_SHA256,
        used_path,
        A52XQ_V3_MARKER,
        A52XQ_V3_PANIC);

    if (!header_len || header_len >= A52XQ_HEADER_BYTES) {
        ret = -EOVERFLOW;
        pr_emerg("A52XQ_V3_LOGDUMP_HEADER_BUILD_FAILED len=%zu\n",
                 header_len);
        goto out_close;
    }

    /* Write payload first. The magic header is the final commit record. */
    pos = A52XQ_SLOT_OFFSET + A52XQ_HEADER_BYTES;
    ret = a52xq_write_full(file, record + A52XQ_HEADER_BYTES,
                           payload_len, &pos);
    if (ret < 0) {
        pr_emerg("A52XQ_V3_LOGDUMP_PAYLOAD_WRITE_FAILED ret=%d\n", ret);
        goto out_close;
    }

    ret = a52xq_flush_logdump(file);
    if (ret) {
        pr_emerg("A52XQ_V3_LOGDUMP_PAYLOAD_FLUSH_FAILED ret=%d\n", ret);
        goto out_close;
    }

    pos = A52XQ_SLOT_OFFSET;
    ret = a52xq_write_full(file, record, A52XQ_HEADER_BYTES, &pos);
    if (ret < 0) {
        pr_emerg("A52XQ_V3_LOGDUMP_HEADER_WRITE_FAILED ret=%d\n", ret);
        goto out_close;
    }

    ret = a52xq_flush_logdump(file);
    if (ret) {
        pr_emerg("A52XQ_V3_LOGDUMP_HEADER_FLUSH_FAILED ret=%d\n", ret);
        goto out_close;
    }

    memset(record, 0, A52XQ_SLOT_BYTES);
    pos = A52XQ_SLOT_OFFSET;
    ret = a52xq_read_full(file, record,
                          A52XQ_HEADER_BYTES + payload_len, &pos);
    if (ret < 0) {
        pr_emerg("A52XQ_V3_LOGDUMP_READBACK_FAILED ret=%d\n", ret);
        goto out_close;
    }

    if (memcmp(record, A52XQ_V3_MAGIC, strlen(A52XQ_V3_MAGIC))) {
        ret = -EBADMSG;
        pr_emerg("A52XQ_V3_LOGDUMP_MAGIC_VERIFY_FAILED\n");
        goto out_close;
    }

    readback_hash = a52xq_fnv1a32(record + A52XQ_HEADER_BYTES,
                                  payload_len);
    if (readback_hash != expected_hash) {
        ret = -EBADMSG;
        pr_emerg("A52XQ_V3_LOGDUMP_HASH_VERIFY_FAILED expected=%08x got=%08x\n",
                 expected_hash, readback_hash);
        goto out_close;
    }

    pr_emerg("%s path=%s payload_len=%zu fnv1a32=%08x\n",
             A52XQ_V3_WRITE_OK, used_path, payload_len, readback_hash);
    ret = 0;

out_close:
    filp_close(file, NULL);
out_free:
    vfree(record);
    return ret;
}

static int __init a52xq_hybrid_probe_init(void)
{
    int i;
    int ret;

    panic_timeout = 0;

    for (i = 0; i < 16; i++) {
        pr_emerg("%s iteration=%d\n", A52XQ_V3_MARKER, i);
        mdelay(25);
    }

    ret = a52xq_write_logdump_record();
    if (ret)
        pr_emerg("%s ret=%d\n", A52XQ_V3_WRITE_REFUSED, ret);

    pr_emerg("%s logdump_result=%d\n", A52XQ_V3_PANIC, ret);
    mdelay(3000);
    panic("%s logdump_result=%d", A52XQ_V3_PANIC, ret);
    return 0;
}
late_initcall_sync(a52xq_hybrid_probe_init);
'''

text = head + start + probe + end + tail

replacements = {
    'info "Installing non-rebooting late-init panic probe v2"':
        'info "Installing guarded logdump late-init probe v3"',
    '# A52XQ hybrid GKI persistent-log probe v2':
        '# A52XQ hybrid GKI guarded logdump probe v3',
    '-a52xq-hybrid-probe-v2-': '-a52xq-hybrid-logdump-v3-',
    'a52xq-hybrid-probe-v2': 'a52xq-hybrid-logdump-v3',
    'config-android14-6.1-a52xq-hybrid-probe-v2':
        'config-android14-6.1-a52xq-hybrid-logdump-v3',
    'Image-android14-6.1-a52xq-hybrid-probe-v2':
        'Image-android14-6.1-a52xq-hybrid-logdump-v3',
    'Image.gz-android14-6.1-a52xq-hybrid-probe-v2':
        'Image.gz-android14-6.1-a52xq-hybrid-logdump-v3',
    'System.map-android14-6.1-a52xq-hybrid-probe-v2':
        'System.map-android14-6.1-a52xq-hybrid-logdump-v3',
    'ack-6.1-probe-v2.sha256': 'ack-6.1-logdump-probe-v3.sha256',
    'ack-6.1-probe-v2-build.txt': 'ack-6.1-logdump-probe-v3-build.txt',
    'probe_revision=v2-non-rebooting-panic':
        'probe_revision=v3-guarded-logdump-writer',
    'probe_revision=v2': 'probe_revision=v3-logdump',
    'probe_action=normal-panic-path-with-pstore-kmsg-dump':
        'probe_action=guarded-pre-panic-kmsg-write-to-logdump',
    'expected_terminal_state=panic-hang-until-manual-recovery-reboot':
        'expected_terminal_state=logdump-write-then-panic-hang',
    'ramoops_device_name=b1b00000.ramoops':
        'logdump_target=/dev/sde18 offset=65011712 bytes=2097152',
    'info "ACK 6.1 hybrid persistent-log probe v2 build completed"':
        'info "ACK 6.1 guarded logdump probe v3 build completed"',
}

for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"missing replacement anchor: {old}")
    text = text.replace(old, new)

marker_start = 'marker_file="$ARTIFACTS_DIR/probe-v2-markers.txt"\n'
marker_end = '\ndone\n\ninfo "Building Qualcomm DTBs as a platform-source sanity check"'
if marker_start not in text:
    raise SystemExit("v2 marker block start not found")
prefix, rest = text.split(marker_start, 1)
if marker_end not in rest:
    raise SystemExit("v2 marker block end not found")
_, suffix = rest.split(marker_end, 1)
marker_block = '''marker_file="$ARTIFACTS_DIR/probe-v3-logdump-markers.txt"
: > "$marker_file"
for marker in \\
  A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_REACHED_LATE_INIT \\
  A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WRITE_VERIFIED \\
  A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WRITE_REFUSED \\
  A52XQ_HYBRID_GKI_6_1_V3_INTENTIONAL_PANIC_NO_AUTO_REBOOT \\
  A52XQ_LOGDUMP_V3 \\
  5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee; do
  strings "$IMAGE" | grep -F "$marker" | head -n1 >> "$marker_file" || \\
    fail "Probe marker is absent from Image: $marker"
done'''
text = prefix + marker_block + marker_end + suffix

old_meta = '''  printf 'probe_marker=A52XQ_HYBRID_GKI_6_1_V2_REACHED_LATE_INIT\\n'
  printf 'probe_panic=A52XQ_HYBRID_GKI_6_1_V2_INTENTIONAL_PANIC_NO_AUTO_REBOOT\\n'
'''
new_meta = '''  printf 'probe_marker=A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_REACHED_LATE_INIT\\n'
  printf 'probe_success=A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WRITE_VERIFIED\\n'
  printf 'probe_refusal=A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WRITE_REFUSED\\n'
  printf 'probe_panic=A52XQ_HYBRID_GKI_6_1_V3_INTENTIONAL_PANIC_NO_AUTO_REBOOT\\n'
  printf 'logdump_partition_bytes=67108864\\n'
  printf 'logdump_slot_offset=65011712\\n'
  printf 'logdump_slot_bytes=2097152\\n'
  printf 'logdump_header_bytes=4096\\n'
  printf 'logdump_original_slot_sha256=5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee\\n'
'''
if old_meta not in text:
    raise SystemExit("v2 metadata marker block not found")
text = text.replace(old_meta, new_meta, 1)

path.write_text(text)
PY

bash -n "$TARGET"
chmod +x "$NEXT"
exec "$NEXT"
