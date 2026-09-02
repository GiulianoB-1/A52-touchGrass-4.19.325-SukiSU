#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_SERIES=4.19
EXEC_C="$KERNEL_DIR/fs/exec.c"
PATCH_OUT="$ARTIFACTS_DIR/resukisu-v4.1.0-exec-hook.patch"
REPORT="$ARTIFACTS_DIR/resukisu-v4.1.0-exec-hook.txt"

 test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
 current_version=$(kernel_version)
 case "$current_version" in
   "$TARGET_SERIES".*) ;;
   *) fail "Expected Linux $TARGET_SERIES.x, found $current_version" ;;
 esac
 test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass commit"
 test -f "$EXEC_C" || fail "fs/exec.c is missing"

info "Replacing legacy SukiSU exec dispatch with the ReSukiSU manual hook"
python3 - "$EXEC_C" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    "#ifdef CONFIG_KSU\n"
    "extern bool ksu_execveat_hook __read_mostly;\n"
    "extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr, void *argv,\n"
    "\t\t\tvoid *envp, int *flags);\n"
    "extern int ksu_handle_execveat_sucompat(int *fd, struct filename **filename_ptr,\n"
    "\t\t\t\t void *argv, void *envp, int *flags);\n"
    "#endif\n",
    "#ifdef CONFIG_KSU_MANUAL_HOOK\n"
    "__attribute__((hot))\n"
    "extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,\n"
    "\t\t\t\tvoid *argv, void *envp, int *flags);\n"
    "#endif\n",
    "exec hook declarations",
)

replace_once(
    "#ifdef CONFIG_KSU\n"
    "\tif (unlikely(ksu_execveat_hook))\n"
    "\t\tksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);\n"
    "\telse\n"
    "\t\tksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL);\n"
    "#endif\n",
    "#ifdef CONFIG_KSU_MANUAL_HOOK\n"
    "\tksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);\n"
    "#endif\n",
    "native execve hook",
)

replace_once(
    "#ifdef CONFIG_KSU\n"
    "\tif (!ksu_execveat_hook)\n"
    "\t\tksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL); /* 32-bit su */\n"
    "#endif\n",
    "#ifdef CONFIG_KSU_MANUAL_HOOK\n"
    "\tksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0); /* 32-bit ksud and 32-on-64 */\n"
    "#endif\n",
    "compat execve hook",
)

path.write_text(text)
PY

! grep -Fq 'ksu_execveat_hook' "$EXEC_C" || fail "Legacy ksu_execveat_hook remains"
! grep -Fq 'ksu_handle_execveat_sucompat' "$EXEC_C" || fail "SUSFS-only exec helper remains"
grep -Fq 'CONFIG_KSU_MANUAL_HOOK' "$EXEC_C" || fail "Manual-hook guard is missing"
test "$(grep -Fc 'ksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);' "$EXEC_C")" -eq 2 \
  || fail "Expected native and compat ReSukiSU exec hooks"

git -C "$KERNEL_DIR" diff --check -- fs/exec.c
git -C "$KERNEL_DIR" diff --binary -- fs/exec.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "Exec-hook patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$current_version"
  printf 'target_series=%s.x\n' "$TARGET_SERIES"
  printf 'touchgrass_commit=%s\n' "$TOUCHGRASS_COMMIT"
  printf 'hook_guard=CONFIG_KSU_MANUAL_HOOK\n'
  printf 'native_execve=ksu_handle_execveat\n'
  printf 'compat_execve=ksu_handle_execveat\n'
  printf 'removed_legacy_symbols=ksu_execveat_hook,ksu_handle_execveat_sucompat\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "ReSukiSU manual exec hook applied"