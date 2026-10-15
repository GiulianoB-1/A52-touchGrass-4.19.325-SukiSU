#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/api-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying Linux $TARGET_VERSION API compatibility repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_exact(path: Path, old: str, new: str, expected: int, label: str):
    text = path.read_text()
    count = text.count(old)
    if count == expected:
        path.write_text(text.replace(old, new))
        repairs.append(label)
        return
    if count == 0 and text.count(new) == expected:
        return
    raise SystemExit(
        f"{label} anchor mismatch: old={count}, new={text.count(new)}, expected={expected}"
    )


# ext4_find_inline_entry() was converted to embed its inode location in 'is',
# but two ext4_search_dir() calls retained the removed standalone 'iloc'.
replace_exact(
    root / "fs/ext4/inline.c",
    "ret = ext4_search_dir(iloc.bh, inline_start, inline_size,\n",
    "ret = ext4_search_dir(is.iloc.bh, inline_start, inline_size,\n",
    2,
    "fs/ext4/inline.c=used-embedded-is-iloc-buffer",
)

# Linux 4.19.325 extended timer_expire_entry with the clock value already
# passed into call_timer_fn(). Preserve Samsung's debug logging around it.
replace_exact(
    root / "kernel/time/timer.c",
    "\ttrace_timer_expire_entry(timer);\n",
    "\ttrace_timer_expire_entry(timer, baseclk);\n",
    1,
    "kernel/time/timer.c=passed-baseclk-to-expire-trace",
)

# The IMA mmap hook now evaluates requested, adjusted, and flag values. The
# security wrapper already computes prot_adj, so pass the complete API tuple.
replace_exact(
    root / "security/security.c",
    "\treturn ima_file_mmap(file, prot);\n",
    "\treturn ima_file_mmap(file, prot, prot_adj, flags);\n",
    1,
    "security/security.c=passed-complete-ima-mmap-arguments",
)

# Exact postconditions.
ext4 = (root / "fs/ext4/inline.c").read_text()
timer = (root / "kernel/time/timer.c").read_text()
security = (root / "security/security.c").read_text()
if ext4.count("ret = ext4_search_dir(is.iloc.bh, inline_start, inline_size,\n") != 2:
    raise SystemExit("ext4 embedded iloc repair failed")
if "ret = ext4_search_dir(iloc.bh, inline_start, inline_size,\n" in ext4:
    raise SystemExit("stale ext4 iloc reference remains")
if timer.count("\ttrace_timer_expire_entry(timer, baseclk);\n") != 1:
    raise SystemExit("timer trace API repair failed")
if security.count("\treturn ima_file_mmap(file, prot, prot_adj, flags);\n") != 1:
    raise SystemExit("IMA mmap API repair failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "Linux $TARGET_VERSION API compatibility repairs applied"
