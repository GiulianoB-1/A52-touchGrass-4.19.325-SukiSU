#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
DISPATCH_C="$SUKISU_DIR/kernel/supercall/dispatch.c"
SUPERCALL_C="$SUKISU_DIR/kernel/supercall/supercall.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-supercall.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-supercall.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before supercall compatibility patch"
test -f "$DISPATCH_C" || fail "SukiSU supercall dispatch.c is missing"
test -f "$SUPERCALL_C" || fail "SukiSU supercall supercall.c is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"

# Verify the exact legacy scheduler/task-work API before changing SukiSU.
grep -Fq 'extern rwlock_t tasklist_lock;' "$KERNEL_DIR/include/linux/sched/task.h" || fail "Linux 4.19 tasklist_lock declaration is missing"
grep -Fq 'extern struct task_struct init_task;' "$KERNEL_DIR/include/linux/sched/task.h" || fail "Linux 4.19 init_task declaration is missing"
grep -Fq 'static inline struct pid *task_pgrp(struct task_struct *task)' "$KERNEL_DIR/include/linux/sched/signal.h" || fail "Linux 4.19 task_pgrp helper is missing"
grep -Fq 'static inline struct pid *task_session(struct task_struct *task)' "$KERNEL_DIR/include/linux/sched/signal.h" || fail "Linux 4.19 task_session helper is missing"
grep -Fq 'extern void change_pid(struct task_struct *task, enum pid_type,' "$KERNEL_DIR/include/linux/pid.h" || fail "Linux 4.19 change_pid declaration is missing"
grep -Fq 'int task_work_add(struct task_struct *task, struct callback_head *twork, bool);' "$KERNEL_DIR/include/linux/task_work.h" || fail "Linux 4.19 Boolean task_work_add API is missing"
! grep -RFn 'TWA_RESUME' "$KERNEL_DIR/include" >/dev/null 2>&1 || fail "Kernel unexpectedly defines TWA_RESUME; review patch"

info "Adapting SukiSU supercall scheduler and task-work APIs to Linux 4.19"
python3 - "$DISPATCH_C" "$SUPERCALL_C" <<'PY'
from pathlib import Path
import sys

dispatch_path = Path(sys.argv[1])
supercall_path = Path(sys.argv[2])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


dispatch = dispatch_path.read_text()
dispatch = replace_once(
    dispatch,
    '#include <linux/thread_info.h>\n',
    '#include <linux/thread_info.h>\n'
    '#include <linux/pid.h>\n'
    '#include <linux/sched/signal.h>\n'
    '#include <linux/sched/task.h>\n',
    'dispatch.c legacy scheduler includes',
)
dispatch_path.write_text(dispatch)

supercall = supercall_path.read_text()
supercall = replace_once(
    supercall,
    '        if (task_work_add(current, &tw->cb, TWA_RESUME)) {\n',
    '        if (task_work_add(current, &tw->cb, true)) { /* Linux 4.19 notify-resume API. */\n',
    'supercall.c task_work_add notification mode',
)
supercall_path.write_text(supercall)
PY

grep -Fq '#include <linux/pid.h>' "$DISPATCH_C" || fail "PID API header was not added"
grep -Fq '#include <linux/sched/signal.h>' "$DISPATCH_C" || fail "Scheduler signal header was not added"
grep -Fq '#include <linux/sched/task.h>' "$DISPATCH_C" || fail "Scheduler task header was not added"
grep -Fq 'write_lock_irq(&tasklist_lock);' "$DISPATCH_C" || fail "Task-list locking path changed unexpectedly"
grep -Fq 'task_pgrp(&init_task)' "$DISPATCH_C" || fail "Init process-group lookup changed unexpectedly"
grep -Fq 'task_session(p) != task_session(&init_task)' "$DISPATCH_C" || fail "Session validation changed unexpectedly"
grep -Fq 'change_pid(p, PIDTYPE_PGID, init_group);' "$DISPATCH_C" || fail "Linux 4.19 change_pid branch is missing"
! grep -Fq 'TWA_RESUME' "$SUPERCALL_C" || fail "Unsupported TWA_RESUME remains"
grep -Fq 'task_work_add(current, &tw->cb, true)' "$SUPERCALL_C" || fail "Boolean task-work notification mode is missing"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- \
  kernel/supercall/dispatch.c \
  kernel/supercall/supercall.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 supercall patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'tasklist_lock_header=linux/sched/task.h\n'
  printf 'init_task_header=linux/sched/task.h\n'
  printf 'task_pgrp_header=linux/sched/signal.h\n'
  printf 'task_session_header=linux/sched/signal.h\n'
  printf 'change_pid_header=linux/pid.h\n'
  printf 'task_work_mode=boolean-notify-true\n'
  printf 'supercall_semantics=unchanged\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 supercall compatibility patch applied"
