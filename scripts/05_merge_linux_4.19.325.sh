#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_TAG=v4.19.154
TO_TAG="$LINUX_STABLE_TARGET_TAG"
FROM_VERSION=${FROM_TAG#v}
TARGET_VERSION=${TO_TAG#v}
STABLE_DIR="$WORKSPACE/linux-stable-direct-$TARGET_VERSION"
BASE_TREE="$WORKSPACE/linux-base-$FROM_VERSION"
THEIRS_TREE="$WORKSPACE/linux-theirs-$TARGET_VERSION"
STATUS_FILE="$ARTIFACTS_DIR/direct-merge-name-status-$TO_TAG.zlist"
CONFLICT_LIST="$ARTIFACTS_DIR/direct-merge-conflicts-$TO_TAG.txt"
CONFLICT_ARCHIVE="$ARTIFACTS_DIR/direct-merge-conflicts-$TO_TAG.tar.gz"
POLICY_LOG="$ARTIFACTS_DIR/direct-merge-policy-$TO_TAG.tsv"
REPORT="$ARTIFACTS_DIR/direct-merge-$TO_TAG.txt"

cleanup_report() {
  mkdir -p "$ARTIFACTS_DIR"
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-$TO_TAG.txt" 2>/dev/null || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-$TO_TAG.stat.txt" 2>/dev/null || true
  if test -s "$CONFLICT_LIST"; then
    tar --ignore-failed-read -C "$KERNEL_DIR" -czf "$CONFLICT_ARCHIVE" -T "$CONFLICT_LIST" 2>/dev/null || true
  fi
}
trap cleanup_report EXIT

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"
test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass base commit"
test "$(kernel_version)" = "$FROM_VERSION" || fail "Expected Linux $FROM_VERSION before direct merge"
test -f "$(dirname "$0")/05_merge_linux_4.19.325.py" || fail "Direct merge policy resolver is missing"
if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Pre-existing reject files must be resolved before the direct merge"
fi

info "Fetching official Linux stable endpoints $FROM_TAG and $TO_TAG"
rm -rf "$STABLE_DIR" "$BASE_TREE" "$THEIRS_TREE"
git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"

from_sha=$(git -C "$STABLE_DIR" rev-parse "$FROM_TAG^{commit}")
to_sha=$(git -C "$STABLE_DIR" rev-parse "$TO_TAG^{commit}")
test "$from_sha" = "f5d8eef067acee3fda37137f4a08c0d3f6427a8e" || fail "Unexpected $FROM_TAG commit: $from_sha"
test "$to_sha" = "$LINUX_STABLE_TARGET_COMMIT" || fail "Unexpected $TO_TAG commit: $to_sha"

mkdir -p "$BASE_TREE" "$THEIRS_TREE"
git -C "$STABLE_DIR" archive "$FROM_TAG" | tar -x -C "$BASE_TREE"
git -C "$STABLE_DIR" archive "$TO_TAG" | tar -x -C "$THEIRS_TREE"
git -C "$STABLE_DIR" diff --name-status -z --no-renames "$FROM_TAG" "$TO_TAG" > "$STATUS_FILE"
git -C "$STABLE_DIR" diff --stat "$FROM_TAG" "$TO_TAG" > "$ARTIFACTS_DIR/upstream-diff-$TO_TAG.stat.txt"
git -C "$STABLE_DIR" rev-list --count "$FROM_TAG..$TO_TAG" > "$ARTIFACTS_DIR/upstream-commit-count-$TO_TAG.txt"

info "Three-way merging Samsung touchGrass changes with Linux $TARGET_VERSION"
python3 "$(dirname "$0")/05_merge_linux_4.19.325.py" \
  "$KERNEL_DIR" "$BASE_TREE" "$THEIRS_TREE" "$STATUS_FILE" \
  "$CONFLICT_LIST" "$REPORT" "$POLICY_LOG" "$from_sha" "$to_sha"

# The direct diff3 merge may preserve vendor halves of conflicts while taking
# upstream callers from other files. That can compile every object but leave
# vmlinux with unresolved symbols. Keep these tightly coupled generic core
# subsystems internally consistent by taking their complete 4.19.325 versions.
# Device-specific Samsung and Qualcomm drivers remain vendor-preserved.
LINK_CLOSURE_REPORT="$ARTIFACTS_DIR/link-closure-upstream-$TO_TAG.txt"
: > "$LINK_CLOSURE_REPORT"
while IFS= read -r rel; do
  test -f "$THEIRS_TREE/$rel" || fail "Missing upstream linker-closure file: $rel"
  install -D -m 0644 "$THEIRS_TREE/$rel" "$KERNEL_DIR/$rel"
  printf '%s\n' "$rel" >> "$LINK_CLOSURE_REPORT"
done <<'EOF'
init/main.c
init/initramfs.c
init/Makefile
kernel/time/timer.c
kernel/sched/cpufreq_schedutil.c
fs/proc/internal.h
fs/proc/inode.c
fs/proc/generic.c
fs/proc/proc_net.c
fs/proc/Makefile
fs/ext4/Makefile
fs/ext4/block_validity.c
fs/crypto/fname.c
fs/crypto/Makefile
lib/Makefile
drivers/char/random.c
drivers/iommu/io-pgtable-arm.c
drivers/mmc/core/core.c
drivers/mmc/core/core.h
drivers/mmc/core/host.c
drivers/mmc/core/mmc.c
drivers/mmc/core/Makefile
drivers/mmc/host/sdhci.c
EOF

info "Repairing vendor compatibility issues exposed by the newer tree"
python3 - \
  "$KERNEL_DIR/drivers/char/Kconfig" \
  "$KERNEL_DIR/arch/arm64/include/asm/sections.h" \
  "$ARTIFACTS_DIR/compat-repairs-$TO_TAG.txt" <<'PY'
from pathlib import Path
import re
import sys

char_kconfig = Path(sys.argv[1])
sections = Path(sys.argv[2])
report = Path(sys.argv[3])
repairs = []

text = char_kconfig.read_text()
menus = len(re.findall(r"^\s*menu(?:\s|$)", text, flags=re.MULTILINE))
endmenus = len(re.findall(r"^\s*endmenu(?:\s|$)", text, flags=re.MULTILINE))
if menus == 1 and endmenus == 0:
    char_kconfig.write_text(text.rstrip() + "\n\nendmenu\n")
    repairs.append(
        "drivers/char/Kconfig=appended-missing-endmenu;"
        "reason=vendor-cross-file-close-rejected-by-new-parser"
    )
elif menus != endmenus:
    raise SystemExit(
        f"unexpected drivers/char/Kconfig menu balance: menu={menus}, endmenu={endmenus}"
    )

text = sections.read_text()
block = (
    "static inline size_t entry_tramp_text_size(void)\n"
    "{\n"
    "\treturn __entry_tramp_text_end - __entry_tramp_text_start;\n"
    "}\n"
)
count = text.count(block)
if count == 2:
    first = text.index(block)
    second = text.index(block, first + len(block))
    text = text[:second] + text[second + len(block):]
    while "\n\n\n#endif /* __ASM_SECTIONS_H */" in text:
        text = text.replace(
            "\n\n\n#endif /* __ASM_SECTIONS_H */",
            "\n\n#endif /* __ASM_SECTIONS_H */",
        )
    sections.write_text(text)
    repairs.append(
        "arch/arm64/include/asm/sections.h=removed-duplicate-entry_tramp_text_size;"
        "reason=touchgrass-and-upstream-4.19.325-added-identical-helper"
    )
elif count != 1:
    raise SystemExit(
        f"unexpected entry_tramp_text_size definition count after merge: {count}"
    )

report.write_text("\n".join(repairs or ["repairs=none"]) + "\n")
PY

current_version=$(kernel_version)
test "$current_version" = "$TARGET_VERSION" || fail "Merged tree reports Linux $current_version instead of $TARGET_VERSION"

python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/remaining-conflict-markers.txt" <<'PY'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
raw = subprocess.check_output(["git", "-C", str(root), "diff", "--name-only", "-z"])
paths = [x.decode("utf-8", "surrogateescape") for x in raw.split(b"\0") if x]
markers = (b"<<<<<<< ", b"||||||| ", b">>>>>>> ")
hits = []
for rel in paths:
    path = root / rel
    if not path.is_file() or path.is_symlink():
        continue
    data = path.read_bytes().splitlines()
    for line_no, line in enumerate(data, 1):
        if line.startswith(markers):
            hits.append(f"{rel}:{line_no}:{line.decode(errors='replace')}\n")
report.write_text("".join(hits))
if hits:
    raise SystemExit(f"{len(hits)} conflict markers remain after policy resolution")
PY

# Preserve whitespace diagnostics without rejecting exact upstream stable content.
git -C "$KERNEL_DIR" diff --check > "$ARTIFACTS_DIR/direct-merge-diff-check.txt" 2>&1 || true
rm -rf "$STABLE_DIR" "$BASE_TREE" "$THEIRS_TREE"
info "Direct Linux stable merge completed: $FROM_VERSION -> $TARGET_VERSION"
