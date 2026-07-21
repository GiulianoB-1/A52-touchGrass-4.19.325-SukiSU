#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/ext4-panic-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying ext4 lookup and panic warning repairs"
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


# The inline lookup path has two nested if statements. The direct merge kept
# only the closing brace for has_inline_data, leaving the rest of
# __ext4_find_entry() inside ext4_has_inline_data(). Restore the outer scope.
path = root / "fs/ext4/namei.c"
text = path.read_text()
old = (
    "\t\tif (has_inline_data) {\n"
    "\t\t\tif (inlined)\n"
    "\t\t\t\t*inlined = 1;\n"
    "\t\t\tgoto cleanup_and_exit;\n"
    "\t}\n\n"
    "\tif ((namelen <= 2)"
)
new = (
    "\t\tif (has_inline_data) {\n"
    "\t\t\tif (inlined)\n"
    "\t\t\t\t*inlined = 1;\n"
    "\t\t\tgoto cleanup_and_exit;\n"
    "\t\t}\n"
    "\t}\n\n"
    "\tif ((namelen <= 2)"
)
if old in text:
    text = replace_once(text, old, new, "ext4 inline lookup scope")
    path.write_text(text)
    repairs.append("fs/ext4/namei.c=closed-inline-data-lookup-scope")
elif new not in text:
    raise SystemExit("ext4 inline lookup scope is neither old nor repaired")

# Restore the complete stable warn_limit implementation. The merged tree kept
# check_panic_on_warn() and warn_limit, but omitted the counter and its public
# sysctl/sysfs plumbing.
path = root / "kernel/panic.c"
text = path.read_text()
warn_block = (
    "#ifdef CONFIG_SYSCTL\n"
    "static struct ctl_table kern_panic_table[] = {\n"
    "\t{\n"
    "\t\t.procname       = \"warn_limit\",\n"
    "\t\t.data           = &warn_limit,\n"
    "\t\t.maxlen         = sizeof(warn_limit),\n"
    "\t\t.mode           = 0644,\n"
    "\t\t.proc_handler   = proc_douintvec,\n"
    "\t},\n"
    "\t{ }\n"
    "};\n\n"
    "static __init int kernel_panic_sysctls_init(void)\n"
    "{\n"
    "\tregister_sysctl_init(\"kernel\", kern_panic_table);\n"
    "\treturn 0;\n"
    "}\n"
    "late_initcall(kernel_panic_sysctls_init);\n"
    "#endif\n\n"
    "static atomic_t warn_count = ATOMIC_INIT(0);\n\n"
    "#ifdef CONFIG_SYSFS\n"
    "static ssize_t warn_count_show(struct kobject *kobj,\n"
    "\t\t\t       struct kobj_attribute *attr, char *page)\n"
    "{\n"
    "\treturn sysfs_emit(page, \"%d\\n\", atomic_read(&warn_count));\n"
    "}\n\n"
    "static struct kobj_attribute warn_count_attr = __ATTR_RO(warn_count);\n\n"
    "static __init int kernel_panic_sysfs_init(void)\n"
    "{\n"
    "\tsysfs_add_file_to_group(kernel_kobj, &warn_count_attr.attr, NULL);\n"
    "\treturn 0;\n"
    "}\n"
    "late_initcall(kernel_panic_sysfs_init);\n"
    "#endif\n\n"
)
if "static atomic_t warn_count = ATOMIC_INIT(0);" not in text:
    anchor = "EXPORT_SYMBOL(panic_notifier_list);\n\n"
    text = replace_once(text, anchor, anchor + warn_block, "panic warn counter block")
    path.write_text(text)
    repairs.append("kernel/panic.c=restored-warn-limit-counter-and-interfaces")
elif text.count("static atomic_t warn_count = ATOMIC_INIT(0);") != 1:
    raise SystemExit("unexpected panic warn_count definition count")

# Exact postconditions.
ext4 = (root / "fs/ext4/namei.c").read_text()
panic = (root / "kernel/panic.c").read_text()
if ext4.count(new) != 1 or old in ext4:
    raise SystemExit("ext4 lookup scope repair failed")
for fragment in (
    "static atomic_t warn_count = ATOMIC_INIT(0);",
    "kernel_panic_sysctls_init",
    "warn_count_show",
    "kernel_panic_sysfs_init",
):
    if panic.count(fragment) < 1:
        raise SystemExit(f"panic warning repair missing {fragment}")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "ext4 lookup and panic warning repairs applied"
