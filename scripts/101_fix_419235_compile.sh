#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
REPORT="$ROOT/artifacts/phase101-419235-compile-repair.txt"

VERSION="$(make -s -C "$KERNEL" kernelversion)"
if [ "$VERSION" != "4.19.235" ]; then
  echo "Expected Linux 4.19.235, got $VERSION" >&2
  exit 1
fi

python3 - "$KERNEL" "$REPORT" <<'PY'
from pathlib import Path
import re
import sys

kernel = Path(sys.argv[1])
report = Path(sys.argv[2])
rows = []


def function_end(source: str, start: int) -> int:
    brace = source.find("{", start)
    if brace < 0:
        raise SystemExit("function opening brace missing")
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise SystemExit("function closing brace missing")


# Linux 4.19.221+ stable commit cf94a8a3ff3d converted pm_show_wakelocks()
# from manual PAGE_SIZE string arithmetic to sysfs_emit_at(). Samsung keeps
# struct wakelock::ws as a pointer. The generic three-way merge can therefore
# take upstream's new `int len` declaration while retaining Samsung's old
# str/end body, leaving str and end undeclared. Canonicalize just this function
# to the stable output logic while preserving the Samsung wakeup-source shape.
wakelock = kernel / "kernel/power/wakelock.c"
text = wakelock.read_text()
sig = "ssize_t pm_show_wakelocks(char *buf, bool show_active)"
if text.count(sig) != 1:
    raise SystemExit("kernel/power/wakelock.c: pm_show_wakelocks signature is not unique")
start = text.index(sig)
end = function_end(text, start)
old_fn = text[start:end]
if "wl->ws->active" in old_fn:
    active = "wl->ws->active"
elif "wl->ws.active" in old_fn:
    active = "wl->ws.active"
else:
    raise SystemExit("kernel/power/wakelock.c: wakeup-source member shape is unrecognized")

new_fn = f'''ssize_t pm_show_wakelocks(char *buf, bool show_active)
{{
\tstruct rb_node *node;
\tstruct wakelock *wl;
\tint len = 0;

\tmutex_lock(&wakelocks_lock);

\tfor (node = rb_first(&wakelocks_tree); node; node = rb_next(node)) {{
\t\twl = rb_entry(node, struct wakelock, node);
\t\tif ({active} == show_active)
\t\t\tlen += sysfs_emit_at(buf, len, "%s ", wl->name);
\t}}
\tlen += sysfs_emit_at(buf, len, "\\n");

\tmutex_unlock(&wakelocks_lock);
\treturn len;
}}'''

if old_fn != new_fn:
    text = text[:start] + new_fn + text[end:]
    wakelock.write_text(text)
    rows.append("wakelock_show=stable-sysfs-emit-with-samsung-ws-shape\n")
else:
    rows.append("wakelock_show=already-repaired\n")

post = wakelock.read_text()
post_start = post.index(sig)
post_end = function_end(post, post_start)
post_fn = post[post_start:post_end]
if "char *str = buf;" in post_fn or "char *end = buf + PAGE_SIZE;" in post_fn:
    raise SystemExit("kernel/power/wakelock.c: legacy buffer pointers remain")
if post_fn.count("sysfs_emit_at(buf, len") != 2:
    raise SystemExit("kernel/power/wakelock.c: sysfs_emit_at postcondition failed")
if "return len;" not in post_fn:
    raise SystemExit("kernel/power/wakelock.c: length return postcondition failed")


# Stable commit 1e1bb4933f1f added FUSE_I_BAD as a private inode-state bit.
# Samsung independently carries FUSE_I_ATTR_FORCE_SYNC and consumes it from
# fuse_dentry_delete().  The policy merge can select either enum tail, so avoid
# newline/last-entry-sensitive incremental insertion.  Instead validate that
# the merged enum contains only the five known states, then rebuild that tiny
# enum canonically with Samsung's bit before the stable bad-inode bit.
fuse_i = kernel / "fs/fuse/fuse_i.h"
text = fuse_i.read_text()
marker = "/** FUSE inode state bits */\nenum {"
if text.count(marker) != 1:
    raise SystemExit("fs/fuse/fuse_i.h: inode-state enum anchor is not unique")
start = text.index(marker)
close = text.find("\n};", start)
if close < 0:
    raise SystemExit("fs/fuse/fuse_i.h: inode-state enum terminator missing")
end = close + len("\n};")
region = text[start:end]

entries = re.findall(r"(?m)^\s*(FUSE_I_[A-Z0-9_]+)\s*,?\s*$", region)
allowed = {
    "FUSE_I_ADVISE_RDPLUS",
    "FUSE_I_INIT_RDPLUS",
    "FUSE_I_SIZE_UNSTABLE",
    "FUSE_I_ATTR_FORCE_SYNC",
    "FUSE_I_BAD",
}
unexpected = sorted(set(entries) - allowed)
if unexpected:
    raise SystemExit(
        "fs/fuse/fuse_i.h: refusing to canonicalize unknown inode-state bits: "
        + ", ".join(unexpected)
    )

for required in (
    "FUSE_I_ADVISE_RDPLUS",
    "FUSE_I_INIT_RDPLUS",
    "FUSE_I_SIZE_UNSTABLE",
):
    count = entries.count(required)
    if count != 1:
        raise SystemExit(
            f"fs/fuse/fuse_i.h: expected one {required} before canonicalization, found {count}"
        )

for optional in ("FUSE_I_ATTR_FORCE_SYNC", "FUSE_I_BAD"):
    count = entries.count(optional)
    if count > 1:
        raise SystemExit(
            f"fs/fuse/fuse_i.h: duplicate {optional} before canonicalization: {count}"
        )

canonical = '''/** FUSE inode state bits */
enum {
\t/** Advise readdirplus  */
\tFUSE_I_ADVISE_RDPLUS,
\t/** Initialized with readdirplus */
\tFUSE_I_INIT_RDPLUS,
\t/** An operation changing file size is in progress  */
\tFUSE_I_SIZE_UNSTABLE,
\t/** Samsung: force dentry invalidation / attribute sync. */
\tFUSE_I_ATTR_FORCE_SYNC,
\t/* Bad inode */
\tFUSE_I_BAD,
};'''

if region != canonical:
    text = text[:start] + canonical + text[end:]
    fuse_i.write_text(text)
    rows.append(
        "fuse_inode_state=canonical-samsung-attr-sync-plus-stable-bad\n"
    )
else:
    rows.append("fuse_inode_state=already-canonical\n")

post = fuse_i.read_text()
post_start = post.index(marker)
post_close = post.find("\n};", post_start)
if post_close < 0:
    raise SystemExit("fs/fuse/fuse_i.h: post-repair enum terminator missing")
post_region = post[post_start:post_close + len("\n};")]
post_entries = re.findall(r"(?m)^\s*(FUSE_I_[A-Z0-9_]+)\s*,?\s*$", post_region)
expected = [
    "FUSE_I_ADVISE_RDPLUS",
    "FUSE_I_INIT_RDPLUS",
    "FUSE_I_SIZE_UNSTABLE",
    "FUSE_I_ATTR_FORCE_SYNC",
    "FUSE_I_BAD",
]
if post_entries != expected:
    raise SystemExit(
        "fs/fuse/fuse_i.h: canonical inode-state postcondition failed: "
        + repr(post_entries)
    )
if "set_bit(FUSE_I_BAD" not in post or "test_bit(FUSE_I_BAD" not in post:
    raise SystemExit("fs/fuse/fuse_i.h: stable bad-inode helpers are incomplete")

# The Samsung bit is not decorative: the vendor dentry delete path actively
# consumes it. Refuse to build if that behavior was dropped by the stable merge.
dir_c = (kernel / "fs/fuse/dir.c").read_text()
if "test_bit(FUSE_I_ATTR_FORCE_SYNC, &fi->state)" not in dir_c:
    raise SystemExit("fs/fuse/dir.c: Samsung FUSE_I_ATTR_FORCE_SYNC consumer is missing")

report.parent.mkdir(parents=True, exist_ok=True)
report.write_text("".join(rows))
PY

git -C "$KERNEL" diff --check
cat "$REPORT"
echo "Phase101 Linux 4.19.235 compile-shape repair complete"
