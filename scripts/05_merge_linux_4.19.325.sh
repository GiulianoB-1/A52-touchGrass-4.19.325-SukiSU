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
REPORT="$ARTIFACTS_DIR/direct-merge-$TO_TAG.txt"

cleanup_report() {
  mkdir -p "$ARTIFACTS_DIR"
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-$TO_TAG.txt" 2>/dev/null || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-$TO_TAG.stat.txt" 2>/dev/null || true
  if test -s "$CONFLICT_LIST"; then
    tar -C "$KERNEL_DIR" -czf "$CONFLICT_ARCHIVE" -T "$CONFLICT_LIST" 2>/dev/null || true
  fi
}
trap cleanup_report EXIT

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"
test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass base commit"
test "$(kernel_version)" = "$FROM_VERSION" || fail "Expected Linux $FROM_VERSION before direct merge"
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
python3 - "$KERNEL_DIR" "$BASE_TREE" "$THEIRS_TREE" "$STATUS_FILE" "$CONFLICT_LIST" "$REPORT" "$from_sha" "$to_sha" <<'PY'
from pathlib import Path
import os
import shutil
import subprocess
import sys

root = Path(sys.argv[1])
base_root = Path(sys.argv[2])
theirs_root = Path(sys.argv[3])
status_file = Path(sys.argv[4])
conflict_list = Path(sys.argv[5])
report = Path(sys.argv[6])
from_sha = sys.argv[7]
to_sha = sys.argv[8]

def exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()

def identity(path: Path):
    if not exists(path):
        return ("missing", b"")
    if path.is_symlink():
        return ("symlink", os.readlink(path).encode())
    return ("file", path.read_bytes())

def remove_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)

def copy_entry(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if exists(destination):
        remove_entry(destination)
    if source.is_symlink():
        os.symlink(os.readlink(source), destination)
    else:
        shutil.copy2(source, destination)

def regular_text(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    return b"\0" not in path.read_bytes()

def symlink_ancestor(path: Path):
    current = path.parent
    while current != root:
        if current.is_symlink():
            return current
        current = current.parent
    return None

raw = status_file.read_bytes().split(b"\0")
if raw and raw[-1] == b"":
    raw.pop()
if len(raw) % 2:
    raise SystemExit(f"unexpected name-status field count: {len(raw)}")

stats = {
    "paths": 0,
    "upstream_taken": 0,
    "already_equal": 0,
    "clean_merges": 0,
    "vendor_deletions": 0,
    "vendor_symlink_paths": 0,
    "conflicts": 0,
}
conflicts = []

for i in range(0, len(raw), 2):
    status = raw[i].decode("ascii", "strict")
    rel = raw[i + 1].decode("utf-8", "surrogateescape")
    stats["paths"] += 1
    ours = root / rel
    base = base_root / rel
    theirs = theirs_root / rel

    # Android vendor kernels intentionally replace some upstream directory trees
    # with symlinks into separate vendor/DTS trees. Preserve that layout and do
    # not materialize upstream files underneath a symlinked parent.
    if symlink_ancestor(ours) is not None:
        stats["vendor_symlink_paths"] += 1
        continue

    ours_id = identity(ours)
    base_id = identity(base)
    theirs_id = identity(theirs)

    if status == "A":
        if ours_id[0] == "missing":
            copy_entry(theirs, ours)
            stats["upstream_taken"] += 1
        elif ours_id == theirs_id:
            stats["already_equal"] += 1
        else:
            conflicts.append(rel)
        continue

    if status == "D":
        if ours_id[0] == "missing":
            stats["already_equal"] += 1
        elif ours_id == base_id:
            remove_entry(ours)
            stats["vendor_deletions"] += 1
        else:
            conflicts.append(rel)
        continue

    if status not in {"M", "T"}:
        conflicts.append(rel)
        continue

    if ours_id == base_id:
        copy_entry(theirs, ours)
        stats["upstream_taken"] += 1
        continue
    if ours_id == theirs_id:
        stats["already_equal"] += 1
        continue
    if ours_id[0] == "missing" or base_id[0] == "missing" or theirs_id[0] == "missing":
        conflicts.append(rel)
        continue
    if not (regular_text(ours) and regular_text(base) and regular_text(theirs)):
        conflicts.append(rel)
        continue

    merged = subprocess.run(
        ["git", "merge-file", "-p", "--diff3", str(ours), str(base), str(theirs)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if merged.returncode > 1:
        raise SystemExit(f"git merge-file failed for {rel}: {merged.stderr.decode(errors='replace')}")
    ours.write_bytes(merged.stdout)
    if merged.returncode == 0:
        stats["clean_merges"] += 1
    else:
        conflicts.append(rel)

stats["conflicts"] = len(conflicts)
conflict_list.write_text("".join(f"{path}\n" for path in conflicts))
report.write_text(
    f"from_tag=v4.19.154\n"
    f"from_commit={from_sha}\n"
    f"to_tag=v4.19.325\n"
    f"to_commit={to_sha}\n"
    + "".join(f"{key}={value}\n" for key, value in stats.items())
    + ("result=conflicts-require-resolution\n" if conflicts else "result=clean-three-way-merge\n")
)
print(report.read_text(), end="")
if conflicts:
    raise SystemExit(f"direct Linux merge left {len(conflicts)} genuine conflicts")
PY

current_version=$(kernel_version)
test "$current_version" = "$TARGET_VERSION" || fail "Merged tree reports Linux $current_version instead of $TARGET_VERSION"
git -C "$KERNEL_DIR" diff --check
rm -rf "$STABLE_DIR" "$BASE_TREE" "$THEIRS_TREE"
info "Direct Linux stable merge completed: $FROM_VERSION -> $TARGET_VERSION"
