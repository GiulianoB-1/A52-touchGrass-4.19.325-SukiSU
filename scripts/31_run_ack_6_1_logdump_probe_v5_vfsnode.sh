#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/29_run_ack_6_1_logdump_probe_v5.sh"
NEXT="$SCRIPT_DIR/30_run_ack_6_1_logdump_probe_v5_trace.sh"

[ -f "$TARGET" ] || {
  echo "Missing v5 wrapper: $TARGET" >&2
  exit 1
}

[ -f "$NEXT" ] || {
  echo "Missing v5 trace wrapper: $NEXT" >&2
  exit 1
}

# The first v5 build reached modpost but correctly failed because the delayed
# work function referenced init_unlink()/init_mknod(), which are discarded with
# .init.text. Replace them with normal VFS helpers that remain valid after init.
python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

include_old = '''#include <linux/init_syscalls.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/mount.h>
'''
include_new = '''#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/mount.h>
#include <linux/namei.h>
'''
if text.count(include_old) != 1:
    raise SystemExit(
        f"v5 include repair: expected one match, found {text.count(include_old)}"
    )
text = text.replace(include_old, include_new, 1)

open_anchor = "open_replacement = r'''static struct file *a52xq_open_logdump(const char **used_path)\n"
open_prefix = r'''open_replacement = r'''static int a52xq_create_tmp_node(dev_t devt)
{
    struct path parent;
    struct dentry *dentry;
    int ret;

    dentry = kern_path_create(AT_FDCWD, A52XQ_TMP_NODE, &parent, 0);
    if (IS_ERR(dentry)) {
        ret = PTR_ERR(dentry);
        pr_emerg("A52XQ_V5_LOGDUMP_TMP_NODE_CREATE_PATH_FAILED ret=%d\n",
                 ret);
        return ret;
    }

    ret = vfs_mknod(mnt_user_ns(parent.mnt), d_inode(parent.dentry),
                    dentry, S_IFBLK | 0600, devt);
    done_path_create(&parent, dentry);
    if (ret)
        pr_emerg("A52XQ_V5_LOGDUMP_TMP_NODE_MKNOD_FAILED ret=%d dev=%u:%u\n",
                 ret, MAJOR(devt), MINOR(devt));

    return ret;
}

static struct file *a52xq_open_logdump(const char **used_path)
'''
if text.count(open_anchor) != 1:
    raise SystemExit(
        f"v5 open helper anchor: expected one match, found {text.count(open_anchor)}"
    )
text = text.replace(open_anchor, open_prefix, 1)

node_old = '''    init_unlink(A52XQ_TMP_NODE);
    ret = init_mknod(A52XQ_TMP_NODE, S_IFBLK | 0600,
                     new_encode_dev(devt));
    if (ret && ret != -EEXIST) {
        pr_emerg("A52XQ_V5_LOGDUMP_TMP_NODE_FAILED ret=%d dev=%u:%u\\n",
                 ret, MAJOR(devt), MINOR(devt));
        return ERR_PTR(ret);
    }
'''
node_new = '''    ret = a52xq_create_tmp_node(devt);
    if (ret)
        return ERR_PTR(ret);
'''
if text.count(node_old) != 1:
    raise SystemExit(
        f"v5 init-node block: expected one match, found {text.count(node_old)}"
    )
text = text.replace(node_old, node_new, 1)

path.write_text(text)
PY

bash -n "$TARGET"
chmod +x "$TARGET" "$NEXT"
exec "$NEXT"
