#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.220
REPORT="$ARTIFACTS_DIR/compile-api-fix-$TARGET_VERSION.txt"
VERIFIER="$KERNEL_DIR/kernel/bpf/verifier.c"
FILE_TABLE="$KERNEL_DIR/fs/file_table.c"
SCHEDUTIL="$KERNEL_DIR/kernel/sched/cpufreq_schedutil.c"
NAMESPACE="$KERNEL_DIR/fs/namespace.c"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"
for path in "$VERIFIER" "$FILE_TABLE" "$SCHEDUTIL" "$NAMESPACE"; do
  test -f "$path" || fail "Required source is missing: $path"
done

info "Repairing Linux $TARGET_VERSION merge-shape compile mismatches"
python3 - "$VERIFIER" "$FILE_TABLE" "$SCHEDUTIL" "$NAMESPACE" "$REPORT" <<'PY'
from pathlib import Path
import sys

verifier = Path(sys.argv[1])
file_table = Path(sys.argv[2])
schedutil = Path(sys.argv[3])
namespace = Path(sys.argv[4])
report = Path(sys.argv[5])
rows = []

# Preserve the reviewed 4.19.200 BPF socket-context backport. The 4.19.220
# three-way merge retained the selector switch but dropped its local function
# pointer and reintroduced a PTR_TO_CTX-only guard, making the socket cases
# unreachable.
text = verifier.read_text()
old = "\tfor (i = 0; i < insn_cnt; i++, insn++) {\n\t\tbool ctx_access;\n"
new = (
    "\tfor (i = 0; i < insn_cnt; i++, insn++) {\n"
    "\t\tbool ctx_access;\n"
    "\t\tbpf_convert_ctx_access_t convert_ctx_access;\n"
)
if text.count(old) != 1:
    raise SystemExit(
        "kernel/bpf/verifier.c: expected one convert_ctx_accesses loop declaration anchor"
    )
text = text.replace(old, new, 1)

stale_guard = (
    "\t\tif (env->insn_aux_data[i + delta].ptr_type != PTR_TO_CTX)\n"
    "\t\t\tcontinue;\n"
)
if text.count(stale_guard) != 1:
    raise SystemExit(
        "kernel/bpf/verifier.c: expected one stale PTR_TO_CTX-only guard"
    )
text = text.replace(stale_guard, "", 1)

if text.count("bpf_convert_ctx_access_t convert_ctx_access;") != 1:
    raise SystemExit("kernel/bpf/verifier.c: converter declaration count is not one")
if "convert_ctx_access = bpf_sock_convert_ctx_access;" not in text:
    raise SystemExit("kernel/bpf/verifier.c: socket converter selection is missing")
if "convert_ctx_access = bpf_tcp_sock_convert_ctx_access;" not in text:
    raise SystemExit("kernel/bpf/verifier.c: TCP socket converter selection is missing")
verifier.write_text(text)
rows.append("bpf_converter_pointer=restored\n")
rows.append("bpf_socket_context_paths=reachable\n")

# Linux 4.19.220 introduced fput_many(file, refs). The merge retained its body
# but attached the old fput(file) signature, then also retained the wrapper,
# producing an undefined refs variable and duplicate fput symbol.
text = file_table.read_text()
broken = (
    "void fput(struct file *file)\n"
    "{\n"
    "\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n"
)
fixed = (
    "void fput_many(struct file *file, unsigned int refs)\n"
    "{\n"
    "\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n"
)
if text.count(broken) != 1:
    raise SystemExit("fs/file_table.c: expected one malformed fput_many body")
text = text.replace(broken, fixed, 1)
if text.count("void fput_many(struct file *file, unsigned int refs)") != 1:
    raise SystemExit("fs/file_table.c: fput_many definition count is not one")
if text.count("void fput(struct file *file)") != 1:
    raise SystemExit("fs/file_table.c: fput wrapper definition count is not one")
if "\tfput_many(file, 1);\n" not in text:
    raise SystemExit("fs/file_table.c: fput wrapper no longer calls fput_many")
file_table.write_text(text)
rows.append("fput_many_signature=restored\n")
rows.append("duplicate_fput=removed\n")

# Upstream 4.19.220 moved tunables lifetime management into the kobject release
# callback. Keep the Samsung per-policy cache, but save the final values before
# gov_attr_set_put() can release the object. Use distinct names for the release
# callback and global pointer cleanup.
text = schedutil.read_text()
release_old = "static void sugov_tunables_free(struct kobject *kobj)\n"
release_new = "static void sugov_tunables_release(struct kobject *kobj)\n"
if text.count(release_old) != 1:
    raise SystemExit("cpufreq_schedutil.c: expected one kobject tunables release callback")
text = text.replace(release_old, release_new, 1)
if text.count("\t.release = &sugov_tunables_free,\n") != 1:
    raise SystemExit("cpufreq_schedutil.c: expected one kobject release assignment")
text = text.replace(
    "\t.release = &sugov_tunables_free,\n",
    "\t.release = &sugov_tunables_release,\n",
    1,
)

cleanup_old = (
    "static void sugov_tunables_free(struct sugov_tunables *tunables)\n"
    "{\n"
    "\tif (!have_governor_per_policy())\n"
    "\t\tglobal_tunables = NULL;\n"
    "}\n"
)
cleanup_new = (
    "static void sugov_clear_global_tunables(void)\n"
    "{\n"
    "\tif (!have_governor_per_policy())\n"
    "\t\tglobal_tunables = NULL;\n"
    "}\n"
)
if text.count(cleanup_old) != 1:
    raise SystemExit("cpufreq_schedutil.c: expected one vendor tunables cleanup helper")
text = text.replace(cleanup_old, cleanup_new, 1)

exit_old = (
    "\tcount = gov_attr_set_put(&tunables->attr_set, &sg_policy->tunables_hook);\n"
    "\tpolicy->governor_data = NULL;\n"
    "\tif (!count) {\n"
    "\t\tsugov_tunables_save(policy, tunables);\n"
    "\t\tsugov_tunables_free(tunables);\n"
    "\t}\n"
)
exit_new = (
    "\t/* gov_attr_set_put() releases tunables when this is the final user. */\n"
    "\tif (tunables->attr_set.usage_count == 1)\n"
    "\t\tsugov_tunables_save(policy, tunables);\n\n"
    "\tcount = gov_attr_set_put(&tunables->attr_set, &sg_policy->tunables_hook);\n"
    "\tpolicy->governor_data = NULL;\n"
    "\tif (!count)\n"
    "\t\tsugov_clear_global_tunables();\n"
)
old_count = text.count(exit_old)
new_count = text.count(exit_new)
if old_count == 1 and new_count == 0:
    text = text.replace(exit_old, exit_new, 1)
    rows.append("schedutil_exit_shape=repaired\n")
elif old_count == 0 and new_count == 1:
    rows.append("schedutil_exit_shape=already-safe\n")
else:
    raise SystemExit(
        f"cpufreq_schedutil.c: unrecognized sugov_exit shape "
        f"(unsafe={old_count}, safe={new_count})"
    )
if text.count("static void sugov_tunables_release(struct kobject *kobj)") != 1:
    raise SystemExit("cpufreq_schedutil.c: kobject release callback count is not one")

definition = "static void sugov_clear_global_tunables(void)"
starts = []
pos = 0
while True:
    pos = text.find(definition, pos)
    if pos < 0:
        break
    starts.append(pos)
    pos += len(definition)

def function_end(source: str, start: int) -> int:
    brace = source.find("{", start)
    if brace < 0:
        raise SystemExit("cpufreq_schedutil.c: cleanup helper opening brace missing")
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                while end < len(source) and source[end] == "\n":
                    end += 1
                return end
    raise SystemExit("cpufreq_schedutil.c: cleanup helper closing brace missing")

if len(starts) > 1:
    first_body = text[starts[0]:function_end(text, starts[0])]
    for start in starts[1:]:
        body = text[start:function_end(text, start)]
        if body != first_body:
            raise SystemExit("cpufreq_schedutil.c: duplicate cleanup helpers are not identical")
    for start in reversed(starts[1:]):
        text = text[:start] + text[function_end(text, start):]
    rows.append(f"schedutil_duplicate_cleanup_helpers_removed={len(starts)-1}\n")

if text.count(definition) != 1:
    raise SystemExit("cpufreq_schedutil.c: global cleanup helper count is not one")
if "sugov_tunables_free(" in text:
    raise SystemExit("cpufreq_schedutil.c: ambiguous sugov_tunables_free name remains")
schedutil.write_text(text)
rows.append("schedutil_kobject_release=separated\n")
rows.append("schedutil_cached_values=saved_before_release\n")

# Use one KDP-aware locked-child helper for both clone_private_mount() and bind
# mount validation. Restore the upstream invalid exit that releases namespace_sem.
text = namespace.read_text()
first_helper = (
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n"
    "\tstruct mount *child;\n\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n"
    "\t\t\tcontinue;\n\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n\n"
)
first_helper_kdp = (
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n"
    "\tstruct mount *child;\n\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n"
    "\t\t\tcontinue;\n\n"
    "#ifdef CONFIG_KDP_NS\n"
    "\t\tif (child->mnt->mnt_flags & MNT_LOCKED)\n"
    "#else\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n"
    "#endif\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n\n"
)
upstream_helper_count = text.count(first_helper)
kdp_helper_count = text.count(first_helper_kdp)
if upstream_helper_count == 1 and kdp_helper_count == 0:
    text = text.replace(first_helper, first_helper_kdp, 1)
    rows.append("namespace_primary_locked_child_helper=converted-to-kdp\n")
elif upstream_helper_count == 0 and kdp_helper_count == 1:
    rows.append("namespace_primary_locked_child_helper=already-kdp\n")
else:
    raise SystemExit(
        f"fs/namespace.c: unrecognized primary locked-child helper shape "
        f"(upstream={upstream_helper_count}, kdp={kdp_helper_count})"
    )

duplicate_helper = (
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n"
    "\tstruct mount *child;\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n"
    "\t\t\tcontinue;\n\n"
    "#ifdef CONFIG_KDP_NS\n"
    "\t\tif (child->mnt->mnt_flags & MNT_LOCKED)\n"
    "#else\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n"
    "#endif\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n\n"
)
duplicate_count = text.count(duplicate_helper)
if duplicate_count == 1:
    text = text.replace(duplicate_helper, "", 1)
    rows.append("namespace_duplicate_locked_child_helper=removed\n")
elif duplicate_count == 0:
    rows.append("namespace_duplicate_locked_child_helper=absent\n")
else:
    raise SystemExit(
        f"fs/namespace.c: duplicate KDP locked-child helper count is {duplicate_count}"
    )

clone_return = (
    "#ifdef CONFIG_KDP_NS\n"
    "\treturn new_mnt->mnt;\n"
    "#else\n"
    "\treturn &new_mnt->mnt;\n"
    "#endif\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(clone_private_mount);\n"
)
clone_fixed = (
    "#ifdef CONFIG_KDP_NS\n"
    "\treturn new_mnt->mnt;\n"
    "#else\n"
    "\treturn &new_mnt->mnt;\n"
    "#endif\n\n"
    "invalid:\n"
    "\tup_read(&namespace_sem);\n"
    "\treturn ERR_PTR(-EINVAL);\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(clone_private_mount);\n"
)
clone_return_count = text.count(clone_return)
clone_fixed_count = text.count(clone_fixed)
if clone_return_count == 1 and clone_fixed_count == 0:
    text = text.replace(clone_return, clone_fixed, 1)
    rows.append("clone_private_mount_invalid_exit=repaired\n")
elif clone_return_count == 0 and clone_fixed_count == 1:
    rows.append("clone_private_mount_invalid_exit=already-present\n")
else:
    raise SystemExit(
        f"fs/namespace.c: unrecognized clone_private_mount exit shape "
        f"(plain={clone_return_count}, fixed={clone_fixed_count})"
    )
if text.count("static bool has_locked_children(struct mount *mnt, struct dentry *dentry)") != 1:
    raise SystemExit("fs/namespace.c: locked-child helper count is not one")
if text.count("invalid:\n\tup_read(&namespace_sem);") != 1:
    raise SystemExit("fs/namespace.c: clone_private_mount invalid exit is missing")
namespace.write_text(text)
rows.append("namespace_locked_child_helper=kdp_aware_single_definition\n")
rows.append("clone_private_mount_invalid_exit=restored\n")

report.write_text("".join(rows))
PY

git -C "$KERNEL_DIR" diff --check
cat "$REPORT"
info "Linux $TARGET_VERSION compile API mismatches repaired"
