#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_SCRIPT="$SCRIPT_DIR/11_qemu_full_stack_stress.sh"
RUNTIME_SCRIPT="$SCRIPT_DIR/.11_qemu_full_stack_stress.scoped.$$"

cleanup() {
  rm -f "$RUNTIME_SCRIPT"
}
trap cleanup EXIT

test -f "$BASE_SCRIPT" || {
  printf 'Missing base QEMU stress script: %s\n' "$BASE_SCRIPT" >&2
  exit 1
}

cp "$BASE_SCRIPT" "$RUNTIME_SCRIPT"

python3 - "$RUNTIME_SCRIPT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    """  FANOTIFY \\
  SCHED_WALT \\
""",
    """  FANOTIFY \\
  TASKSTATS \\
  ARCH_QCOM \\
  COMMON_CLK_QCOM \\
  SCHED_WALT \\
""",
    "extend unused QEMU subsystem disable list",
)

replace_once(
    """(MODULES|EXT4_FS|KPROBES|KSU|PROVE_LOCKING|KASAN|KVM|KEXEC|CRASH_DUMP|NFS_FS|TRANSPARENT_HUGEPAGE|FANOTIFY|SCHED_WALT)""",
    """(MODULES|EXT4_FS|KPROBES|KSU|PROVE_LOCKING|KASAN|KVM|KEXEC|CRASH_DUMP|NFS_FS|TRANSPARENT_HUGEPAGE|FANOTIFY|TASKSTATS|ARCH_QCOM|COMMON_CLK_QCOM|SCHED_WALT)""",
    "extend QEMU key-symbol diagnostics",
)

replace_once(
    """for disabled in KVM KEXEC CRASH_DUMP NFS_FS TRANSPARENT_HUGEPAGE FANOTIFY SCHED_WALT; do""",
    """for disabled in KVM KEXEC CRASH_DUMP NFS_FS TRANSPARENT_HUGEPAGE FANOTIFY TASKSTATS ARCH_QCOM COMMON_CLK_QCOM SCHED_WALT; do""",
    "extend resolved-config disabled validation",
)

replace_once(
    """info "Building generic ARM64 QEMU kernel with $PROFILE diagnostics"\n""",
    """info "Generating SELinux headers for the generic QEMU output tree"
make -C "$KERNEL_DIR" O="$QEMU_OUT" \\
  DTC_EXT="$KERNEL_DIR/tools/dtc" \\
  scripts/selinux/genheaders/
qemu_genheaders="$QEMU_OUT/scripts/selinux/genheaders/genheaders"
test -x "$qemu_genheaders" || fail "QEMU SELinux genheaders tool is missing"
mkdir -p "$QEMU_OUT/security/selinux"
"$qemu_genheaders" \\
  "$QEMU_OUT/security/selinux/flask.h" \\
  "$QEMU_OUT/security/selinux/av_permissions.h"
test -s "$QEMU_OUT/security/selinux/flask.h" || fail "QEMU SELinux flask.h was not generated"
test -s "$QEMU_OUT/security/selinux/av_permissions.h" || fail "QEMU SELinux av_permissions.h was not generated"
sha256sum \\
  "$QEMU_OUT/security/selinux/flask.h" \\
  "$QEMU_OUT/security/selinux/av_permissions.h" \\
  > "$QEMU_ARTIFACT_DIR/qemu-selinux-generated-headers.sha256"

info "Building generic ARM64 QEMU kernel with $PROFILE diagnostics"
""",
    "generate SELinux headers before the generic QEMU Image build",
)

path.write_text(text)
PY

chmod +x "$RUNTIME_SCRIPT"
bash -n "$RUNTIME_SCRIPT"
grep -Fq '  TASKSTATS \' "$RUNTIME_SCRIPT"
grep -Fq '  ARCH_QCOM \' "$RUNTIME_SCRIPT"
grep -Fq '  COMMON_CLK_QCOM \' "$RUNTIME_SCRIPT"
grep -Fq 'TASKSTATS ARCH_QCOM COMMON_CLK_QCOM SCHED_WALT' "$RUNTIME_SCRIPT"
grep -Fq 'qemu_genheaders="$QEMU_OUT/scripts/selinux/genheaders/genheaders"' "$RUNTIME_SCRIPT"
grep -Fq 'qemu-selinux-generated-headers.sha256' "$RUNTIME_SCRIPT"

"$RUNTIME_SCRIPT" "$@"
