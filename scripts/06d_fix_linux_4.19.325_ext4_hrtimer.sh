#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/ext4-hrtimer-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying ext4 and hrtimer Linux $TARGET_VERSION repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: {count}")
    return text.replace(old, new, 1)


# The stable ext4 checker now requires both the logical directory block and the
# byte offset. Thread the actual htree leaf block from do_split() into
# dx_make_map(), rather than inventing a diagnostic block number.
path = root / "fs/ext4/namei.c"
text = path.read_text()
old_proto = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail);\n"
)
new_proto = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       ext4_lblk_t lblk, struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail);\n"
)
old_def = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail)\n"
)
new_def = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       ext4_lblk_t lblk, struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail)\n"
)
old_check = (
    "\t\tif (ext4_check_dir_entry(dir, NULL, de, bh, base, buflen,\n"
    "\t\t\t\t\t ((char *)de) - base))\n"
)
new_check = (
    "\t\tif (ext4_check_dir_entry(dir, NULL, de, bh, base, buflen,\n"
    "\t\t\t\t\t lblk, ((char *)de) - base))\n"
)
old_call = "\tcount = dx_make_map(dir, *bh, hinfo, map);\n"
new_call = (
    "\tcount = dx_make_map(dir, *bh, dx_get_block(frame->at), hinfo, map);\n"
)
if old_proto in text:
    text = replace_once(text, old_proto, new_proto, "ext4 dx_make_map prototype")
    text = replace_once(text, old_def, new_def, "ext4 dx_make_map definition")
    text = replace_once(text, old_check, new_check, "ext4 directory checker call")
    text = replace_once(text, old_call, new_call, "ext4 dx_make_map caller")
    path.write_text(text)
    repairs.append("fs/ext4/namei.c=threaded-logical-block-into-directory-check")
elif not all(item in text for item in (new_proto, new_def, new_check, new_call)):
    raise SystemExit("ext4 dx_make_map API is neither old nor fully repaired")

# __migrate_hrtimers() receives the source/offline CPU as scpu. The direct
# merge retained a later call-site name, ncpu, without its declaration.
path = root / "kernel/time/hrtimer.c"
text = path.read_text()
old = "\tsmp_call_function_single(ncpu, retrigger_next_event, NULL, 0);\n"
new = "\tsmp_call_function_single(scpu, retrigger_next_event, NULL, 0);\n"
if old in text:
    text = replace_once(text, old, new, "hrtimer source CPU retrigger")
    path.write_text(text)
    repairs.append("kernel/time/hrtimer.c=retriggered-source-cpu")
elif text.count(new) != 1:
    raise SystemExit("hrtimer retrigger target is neither old nor repaired")

# Exact postconditions.
ext4 = (root / "fs/ext4/namei.c").read_text()
hrtimer = (root / "kernel/time/hrtimer.c").read_text()
for fragment in (new_proto, new_def, new_check, new_call):
    if ext4.count(fragment) != 1:
        raise SystemExit("ext4 logical block threading repair failed")
if old_check in ext4 or old_call in ext4:
    raise SystemExit("stale ext4 directory checker form remains")
if hrtimer.count(new) != 1 or old in hrtimer:
    raise SystemExit("hrtimer source CPU repair failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "ext4 and hrtimer Linux $TARGET_VERSION repairs applied"
