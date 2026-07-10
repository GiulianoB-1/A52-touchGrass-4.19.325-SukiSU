#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/23_build_ack_6_1_logdump_probe_v3.sh"

[ -f "$TARGET" ] || {
  echo "Missing logdump probe wrapper: $TARGET" >&2
  exit 1
}

# Probe v5 registers a block-class interface before normal device init completes.
# It observes the exact GPT partition label and size, captures the registered
# dev_t, and schedules the guarded writer from workqueue context. No Android
# userspace /dev node or by-name symlink is required.
python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

# Repairs still required by the original v3 wrapper.
repairs = [
    (
        "    'a52xq-hybrid-probe-v2': 'a52xq-hybrid-logdump-v3',",
        "    '--set-str DEFAULT_HOSTNAME a52xq-hybrid-probe-v2': "
        "'--set-str DEFAULT_HOSTNAME a52xq-hybrid-logdump-v3',",
        "generic replacement",
    ),
    (
        "marker_end = '\\ndone\\n\\ninfo \"Building Qualcomm DTBs as a platform-source sanity check\"'",
        "marker_end = '\\n\\ninfo \"Building Qualcomm DTBs as a platform-source sanity check\"'",
        "marker-loop terminator",
    ),
]

for old, new, label in repairs:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} repair anchor: expected one match, found {count}")
    text = text.replace(old, new, 1)

include_old = '''#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
'''
include_new = '''#include <linux/device.h>
#include <linux/init.h>
#include <linux/init_syscalls.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/mount.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
'''
if text.count(include_old) != 1:
    raise SystemExit(f"include anchor: expected one match, found {text.count(include_old)}")
text = text.replace(include_old, include_new, 1)

array_start = '''static const char * const a52xq_logdump_paths[] = {
'''
array_end = '''};

static u32 a52xq_fnv1a32'''
if text.count(array_start) != 1:
    raise SystemExit(f"path-array start: expected one match, found {text.count(array_start)}")
head, rest = text.split(array_start, 1)
if array_end not in rest:
    raise SystemExit("path-array end anchor missing")
_, tail = rest.split(array_end, 1)
array_replacement = '''#define A52XQ_TARGET_LABEL "logdump"
#define A52XQ_TMP_NODE "/a52xq-logdump-v5"

#define A52XQ_V3_WATCH_REGISTERED \\
    "A52XQ_HYBRID_GKI_6_1_V3_BLOCK_WATCH_REGISTERED"
#define A52XQ_V3_PARTITION_SEEN \\
    "A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_PARTITION_SEEN"
#define A52XQ_V3_WORK_STARTED \\
    "A52XQ_HYBRID_GKI_6_1_V3_LOGDUMP_WORK_STARTED"

static DEFINE_SPINLOCK(a52xq_target_lock);
static dev_t a52xq_target_devt;
static bool a52xq_target_scheduled;

static void a52xq_logdump_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52xq_logdump_work, a52xq_logdump_workfn);

static u32 a52xq_fnv1a32'''
text = head + array_replacement + tail

open_start = '''static struct file *a52xq_open_logdump(const char **used_path)
'''
open_end = '''
static int a52xq_flush_logdump'''
if text.count(open_start) != 1:
    raise SystemExit(f"open-function start: expected one match, found {text.count(open_start)}")
head, rest = text.split(open_start, 1)
if open_end not in rest:
    raise SystemExit("open-function end anchor missing")
_, tail = rest.split(open_end, 1)
open_replacement = r'''static struct file *a52xq_open_logdump(const char **used_path)
{
    struct file *file;
    unsigned long flags;
    dev_t devt;
    int retry;
    int ret;
    int last_err = -ENODEV;

    spin_lock_irqsave(&a52xq_target_lock, flags);
    devt = a52xq_target_devt;
    spin_unlock_irqrestore(&a52xq_target_lock, flags);

    if (!devt)
        return ERR_PTR(-ENODEV);

    init_unlink(A52XQ_TMP_NODE);
    ret = init_mknod(A52XQ_TMP_NODE, S_IFBLK | 0600,
                     new_encode_dev(devt));
    if (ret && ret != -EEXIST) {
        pr_emerg("A52XQ_V5_LOGDUMP_TMP_NODE_FAILED ret=%d dev=%u:%u\n",
                 ret, MAJOR(devt), MINOR(devt));
        return ERR_PTR(ret);
    }

    for (retry = 0; retry < 50; retry++) {
        file = filp_open(A52XQ_TMP_NODE,
                         O_RDWR | O_DSYNC | O_LARGEFILE, 0);
        if (!IS_ERR(file)) {
            *used_path = "block-class:PARTLABEL=logdump";
            pr_emerg("A52XQ_V5_LOGDUMP_DEVICE_OPENED dev=%u:%u node=%s\n",
                     MAJOR(devt), MINOR(devt), A52XQ_TMP_NODE);
            return file;
        }

        last_err = PTR_ERR(file);
        msleep(100);
    }

    pr_emerg("A52XQ_V5_LOGDUMP_DEVICE_OPEN_FAILED ret=%d dev=%u:%u\n",
             last_err, MAJOR(devt), MINOR(devt));
    return ERR_PTR(last_err);
}
'''
text = head + open_replacement + open_end + tail

init_start = '''static int __init a52xq_hybrid_probe_init(void)
'''
init_end = '''late_initcall_sync(a52xq_hybrid_probe_init);'''
if text.count(init_start) != 1:
    raise SystemExit(f"init-function start: expected one match, found {text.count(init_start)}")
head, rest = text.split(init_start, 1)
if init_end not in rest:
    raise SystemExit("late-init terminator anchor missing")
_, tail = rest.split(init_end, 1)
init_replacement = r'''static void a52xq_logdump_workfn(struct work_struct *work)
{
    int i;
    int ret;

    (void)work;
    panic_timeout = 0;

    for (i = 0; i < 16; i++) {
        pr_emerg("%s iteration=%d\n", A52XQ_V3_WORK_STARTED, i);
        mdelay(25);
    }

    ret = a52xq_write_logdump_record();
    if (ret)
        pr_emerg("%s ret=%d\n", A52XQ_V3_WRITE_REFUSED, ret);

    pr_emerg("%s logdump_result=%d\n", A52XQ_V3_PANIC, ret);
    mdelay(3000);
    panic("%s logdump_result=%d", A52XQ_V3_PANIC, ret);
}

static int a52xq_block_add(struct device *dev,
                           struct class_interface *interface)
{
    struct block_device *bdev;
    unsigned long flags;
    bool schedule = false;
    const char *label;

    (void)interface;

    bdev = dev_to_bdev(dev);
    if (!bdev || !bdev->bd_meta_info)
        return 0;

    label = (const char *)bdev->bd_meta_info->volname;
    if (strncmp(label, A52XQ_TARGET_LABEL, sizeof(A52XQ_TARGET_LABEL)))
        return 0;

    if (bdev_nr_bytes(bdev) != A52XQ_PARTITION_BYTES) {
        pr_emerg("A52XQ_V5_LOGDUMP_SIZE_MISMATCH_AT_DISCOVERY dev=%u:%u bytes=%llu expected=%llu\n",
                 MAJOR(dev->devt), MINOR(dev->devt),
                 (unsigned long long)bdev_nr_bytes(bdev),
                 (unsigned long long)A52XQ_PARTITION_BYTES);
        return 0;
    }

    spin_lock_irqsave(&a52xq_target_lock, flags);
    if (!a52xq_target_scheduled) {
        a52xq_target_devt = dev->devt;
        a52xq_target_scheduled = true;
        schedule = true;
    }
    spin_unlock_irqrestore(&a52xq_target_lock, flags);

    if (schedule) {
        pr_emerg("%s dev=%u:%u bytes=%llu\n",
                 A52XQ_V3_PARTITION_SEEN,
                 MAJOR(dev->devt), MINOR(dev->devt),
                 (unsigned long long)bdev_nr_bytes(bdev));
        schedule_delayed_work(&a52xq_logdump_work,
                              msecs_to_jiffies(500));
    }

    return 0;
}

static struct class_interface a52xq_block_interface = {
    .class = &block_class,
    .add_dev = a52xq_block_add,
};

static int __init a52xq_hybrid_probe_init(void)
{
    int ret;

    panic_timeout = 0;
    ret = class_interface_register(&a52xq_block_interface);
    if (ret) {
        pr_emerg("A52XQ_V5_BLOCK_WATCH_REGISTER_FAILED ret=%d\n", ret);
        return ret;
    }

    pr_emerg("%s\n", A52XQ_V3_WATCH_REGISTERED);
    return 0;
}
subsys_initcall_sync(a52xq_hybrid_probe_init);'''
text = head + init_replacement + tail

# Promote all probe and artifact identifiers to v5.
replacements = [
    ("Installing guarded logdump late-init probe v3",
     "Installing block-lifecycle logdump probe v5"),
    ("A52XQ hybrid GKI guarded logdump probe v3",
     "A52XQ hybrid GKI block-lifecycle logdump probe v5"),
    ("A52XQ ACK 6.1 diagnostic probe v3",
     "A52XQ ACK 6.1 diagnostic probe v5"),
    ("probe_revision=v3-guarded-logdump-writer",
     "probe_revision=v5-block-lifecycle-writer"),
    ("probe_revision=v3-logdump",
     "probe_revision=v5-block-lifecycle"),
    ("A52XQ_HYBRID_GKI_6_1_V3_", "A52XQ_HYBRID_GKI_6_1_V5_"),
    ("A52XQ_V3_", "A52XQ_V5_"),
    ("A52XQ_LOGDUMP_V3", "A52XQ_LOGDUMP_V5"),
    ("probe-v3-logdump", "probe-v5-logdump"),
    ("hybrid-logdump-v3", "hybrid-logdump-v5"),
    ("logdump-probe-v3", "logdump-probe-v5"),
    ("probe v3", "probe v5"),
    ("Probe v3", "Probe v5"),
]
for old, new in replacements:
    text = text.replace(old, new)

# Add lifecycle markers to the generated Image audit.
slash2 = "\\\\"
marker_anchor = (
    "  A52XQ_HYBRID_GKI_6_1_V5_LOGDUMP_WRITE_REFUSED " + slash2 + "\n"
    "  A52XQ_HYBRID_GKI_6_1_V5_INTENTIONAL_PANIC_NO_AUTO_REBOOT " + slash2 + "\n"
)
marker_replacement = (
    "  A52XQ_HYBRID_GKI_6_1_V5_LOGDUMP_WRITE_REFUSED " + slash2 + "\n"
    "  A52XQ_HYBRID_GKI_6_1_V5_BLOCK_WATCH_REGISTERED " + slash2 + "\n"
    "  A52XQ_HYBRID_GKI_6_1_V5_LOGDUMP_PARTITION_SEEN " + slash2 + "\n"
    "  A52XQ_HYBRID_GKI_6_1_V5_LOGDUMP_WORK_STARTED " + slash2 + "\n"
    "  A52XQ_HYBRID_GKI_6_1_V5_INTENTIONAL_PANIC_NO_AUTO_REBOOT " + slash2 + "\n"
)
if text.count(marker_anchor) != 1:
    raise SystemExit(
        f"v5 marker insertion anchor: expected one match, found {text.count(marker_anchor)}"
    )
text = text.replace(marker_anchor, marker_replacement, 1)

# Record the direct lifecycle strategy in the build manifest.
meta_anchor = "logdump_target=/dev/sde18 offset=65011712 bytes=2097152"
meta_replacement = (
    "logdump_target=block_class PARTLABEL=logdump "
    "temporary_node=/a52xq-logdump-v5 offset=65011712 bytes=2097152"
)
if text.count(meta_anchor) != 1:
    raise SystemExit(
        f"metadata target anchor: expected one match, found {text.count(meta_anchor)}"
    )
text = text.replace(meta_anchor, meta_replacement, 1)

path.write_text(text)
PY

bash -n "$TARGET"
chmod +x "$TARGET"
exec "$TARGET"
