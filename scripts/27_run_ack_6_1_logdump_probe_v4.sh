#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/23_build_ack_6_1_logdump_probe_v3.sh"

[ -f "$TARGET" ] || {
  echo "Missing logdump probe wrapper: $TARGET" >&2
  exit 1
}

# Probe v4 keeps the audited v3 slot format and writer, but fixes the reason v3
# could not write: Android /dev nodes and by-name symlinks do not necessarily
# exist at late_initcall time. Resolve the already-registered GPT partition by
# PARTLABEL (with /dev/sde18 fallback), create a temporary block node using the
# kernel early-init syscall helper, then reuse the guarded v3 writer.
python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

# First apply both syntax/anchor repairs required by the original v3 wrapper.
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

# Add the same early-boot block-resolution helpers used by Linux pstore/blk.
include_old = '''#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
'''
include_new = '''#include <linux/init.h>
#include <linux/init_syscalls.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/mount.h>
'''
if text.count(include_old) != 1:
    raise SystemExit("include anchor mismatch")
text = text.replace(include_old, include_new, 1)

array_start = '''static const char * const a52xq_logdump_paths[] = {
'''
array_end = '''};

static u32 a52xq_fnv1a32'''
if text.count(array_start) != 1:
    raise SystemExit("path-array start anchor mismatch")
head, rest = text.split(array_start, 1)
if array_end not in rest:
    raise SystemExit("path-array end anchor mismatch")
_, tail = rest.split(array_end, 1)
array_replacement = '''#define A52XQ_TMP_NODE "/dev/a52xq-logdump-v4"

static const char * const a52xq_logdump_specs[] = {
    "PARTLABEL=logdump",
    "/dev/sde18",
};

static u32 a52xq_fnv1a32'''
text = head + array_replacement + tail

open_start = '''static struct file *a52xq_open_logdump(const char **used_path)
'''
open_end = '''
static int a52xq_flush_logdump'''
if text.count(open_start) != 1:
    raise SystemExit("open-function start anchor mismatch")
head, rest = text.split(open_start, 1)
if open_end not in rest:
    raise SystemExit("open-function end anchor mismatch")
_, tail = rest.split(open_end, 1)
open_replacement = r'''static struct file *a52xq_open_logdump(const char **used_path)
{
    struct file *file;
    dev_t dev;
    int retry;
    int ret;
    int last_err = -ENODEV;
    size_t i;

    for (retry = 0; retry < 150; retry++) {
        for (i = 0; i < ARRAY_SIZE(a52xq_logdump_specs); i++) {
            dev = name_to_dev_t(a52xq_logdump_specs[i]);
            if (!dev)
                continue;

            init_unlink(A52XQ_TMP_NODE);
            ret = init_mknod(A52XQ_TMP_NODE, S_IFBLK | 0600,
                             new_encode_dev(dev));
            if (ret && ret != -EEXIST) {
                last_err = ret;
                continue;
            }

            file = filp_open(A52XQ_TMP_NODE,
                             O_RDWR | O_DSYNC | O_LARGEFILE, 0);
            if (!IS_ERR(file)) {
                *used_path = a52xq_logdump_specs[i];
                pr_emerg("A52XQ_V4_LOGDUMP_DEVICE_RESOLVED spec=%s dev=%u:%u node=%s\n",
                         *used_path, MAJOR(dev), MINOR(dev),
                         A52XQ_TMP_NODE);
                return file;
            }

            last_err = PTR_ERR(file);
        }
        msleep(100);
    }

    pr_emerg("A52XQ_V4_LOGDUMP_DEVICE_RESOLVE_FAILED ret=%d\n",
             last_err);
    return ERR_PTR(last_err);
}
'''
text = head + open_replacement + open_end + tail

# Promote all probe/artifact identifiers from v3 to v4 after the structural
# edits, while keeping the audited partition geometry unchanged.
for old, new in [
    ("A52XQ_V3_", "A52XQ_V4_"),
    ("A52XQ_LOGDUMP_V3", "A52XQ_LOGDUMP_V4"),
    ("probe-v3-logdump", "probe-v4-logdump"),
    ("hybrid-logdump-v3", "hybrid-logdump-v4"),
    ("logdump-probe-v3", "logdump-probe-v4"),
    ("probe_revision=v3", "probe_revision=v4"),
    ("probe v3", "probe v4"),
    ("Probe v3", "Probe v4"),
]:
    text = text.replace(old, new)

marker_anchor = '''  A52XQ_HYBRID_GKI_6_1_V4_LOGDUMP_WRITE_REFUSED \\
  A52XQ_HYBRID_GKI_6_1_V4_INTENTIONAL_PANIC_NO_AUTO_REBOOT \\
'''
marker_replacement = '''  A52XQ_HYBRID_GKI_6_1_V4_LOGDUMP_WRITE_REFUSED \\
  A52XQ_V4_LOGDUMP_DEVICE_RESOLVED \\
  A52XQ_V4_LOGDUMP_DEVICE_RESOLVE_FAILED \\
  A52XQ_HYBRID_GKI_6_1_V4_INTENTIONAL_PANIC_NO_AUTO_REBOOT \\
'''
if text.count(marker_anchor) != 1:
    raise SystemExit("v4 marker insertion anchor mismatch")
text = text.replace(marker_anchor, marker_replacement, 1)

# Record the resolution strategy in build metadata.
meta_anchor = "  printf 'logdump_target=/dev/sde18 offset=65011712 bytes=2097152\\n'"
meta_replacement = (
    "  printf 'logdump_target=PARTLABEL=logdump fallback=/dev/sde18 "
    "offset=65011712 bytes=2097152\\n'"
)
if text.count(meta_anchor) != 1:
    raise SystemExit("metadata target anchor mismatch")
text = text.replace(meta_anchor, meta_replacement, 1)

path.write_text(text)
PY

bash -n "$TARGET"
chmod +x "$TARGET"
exec "$TARGET"
