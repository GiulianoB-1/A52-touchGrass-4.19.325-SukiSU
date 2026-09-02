#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/oops-limit-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Restoring Linux $TARGET_VERSION oops limit state"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
path = root / "kernel/exit.c"
text = path.read_text()
repairs = []

if "static unsigned int oops_limit = 10000;" not in text:
    anchor = "#include <linux/sec_debug.h>\n\n"
    if anchor not in text:
        raise SystemExit("kernel/exit.c Samsung include anchor is missing")
    block = (
        "/*\n"
        " * Bound repeated kernel oopses before reference counters can wrap.\n"
        " * This is the Linux 4.19.325 stable default and userspace interface.\n"
        " */\n"
        "static unsigned int oops_limit = 10000;\n"
        "\n"
        "#ifdef CONFIG_SYSCTL\n"
        "static struct ctl_table kern_exit_table[] = {\n"
        "\t{\n"
        "\t\t.procname       = \"oops_limit\",\n"
        "\t\t.data           = &oops_limit,\n"
        "\t\t.maxlen         = sizeof(oops_limit),\n"
        "\t\t.mode           = 0644,\n"
        "\t\t.proc_handler   = proc_douintvec,\n"
        "\t},\n"
        "\t{ }\n"
        "};\n"
        "\n"
        "static __init int kernel_exit_sysctls_init(void)\n"
        "{\n"
        "\tregister_sysctl_init(\"kernel\", kern_exit_table);\n"
        "\treturn 0;\n"
        "}\n"
        "late_initcall(kernel_exit_sysctls_init);\n"
        "#endif\n"
        "\n"
        "static atomic_t oops_count = ATOMIC_INIT(0);\n"
        "\n"
    )
    text = text.replace(anchor, anchor + block, 1)
    path.write_text(text)
    repairs.append("kernel/exit.c=restored-oops-limit-sysctl-and-counter")

text = path.read_text()
if text.count("static unsigned int oops_limit = 10000;") != 1:
    raise SystemExit("oops_limit definition validation failed")
if text.count("static atomic_t oops_count = ATOMIC_INIT(0);") != 1:
    raise SystemExit("oops_count definition validation failed")
if text.count('.procname       = "oops_limit"') != 1:
    raise SystemExit("oops_limit sysctl validation failed")
if "atomic_inc_return(&oops_count)" not in text:
    raise SystemExit("make_task_dead does not consume oops_count")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "Linux $TARGET_VERSION oops limit state restored"
