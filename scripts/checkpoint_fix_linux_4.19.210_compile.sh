#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.210
REPORT="$ARTIFACTS_DIR/compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying only the merge-shape repairs present in Linux $TARGET_VERSION"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
rows = []


def write_if_changed(path: Path, old: str, new: str, label: str, expected_max: int = 1):
    text = path.read_text()
    count = text.count(old)
    if count > expected_max:
        raise SystemExit(f"{path.relative_to(root)}: {label} anchor appears {count} times")
    if count == 1:
        path.write_text(text.replace(old, new, 1))
        rows.append(f"{label}=repaired\n")
        return True
    rows.append(f"{label}=not-present\n")
    return False


# BPF merge-shape repair.
path = root / "kernel/bpf/verifier.c"
text = path.read_text()
loop_old = "\tfor (i = 0; i < insn_cnt; i++, insn++) {\n\t\tbool ctx_access;\n"
if loop_old in text and "bpf_convert_ctx_access_t convert_ctx_access;" not in text:
    text = text.replace(loop_old, loop_old + "\t\tbpf_convert_ctx_access_t convert_ctx_access;\n", 1)
    rows.append("bpf_converter_pointer=repaired\n")
else:
    rows.append("bpf_converter_pointer=not-needed\n")
stale_guard = (
    "\t\tif (env->insn_aux_data[i + delta].ptr_type != PTR_TO_CTX)\n"
    "\t\t\tcontinue;\n"
)
if stale_guard in text and "convert_ctx_access = bpf_sock_convert_ctx_access;" in text:
    text = text.replace(stale_guard, "", 1)
    rows.append("bpf_socket_context_guard=repaired\n")
else:
    rows.append("bpf_socket_context_guard=not-needed\n")
path.write_text(text)

# File reference-count helper rename, only when the malformed merge is present.
path = root / "fs/file_table.c"
write_if_changed(
    path,
    "void fput(struct file *file)\n{\n\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n",
    "void fput_many(struct file *file, unsigned int refs)\n{\n\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n",
    "fput_many_signature",
)

# Samsung schedutil lifetime compatibility.
path = root / "kernel/sched/cpufreq_schedutil.c"
text = path.read_text()
if "static void sugov_tunables_free(struct kobject *kobj)" in text:
    text = text.replace(
        "static void sugov_tunables_free(struct kobject *kobj)",
        "static void sugov_tunables_release(struct kobject *kobj)",
        1,
    )
    text = text.replace(
        "\t.release = &sugov_tunables_free,\n",
        "\t.release = &sugov_tunables_release,\n",
        1,
    )
    rows.append("schedutil_release_name=repaired\n")
else:
    rows.append("schedutil_release_name=not-needed\n")
cleanup_old = (
    "static void sugov_tunables_free(struct sugov_tunables *tunables)\n"
    "{\n\tif (!have_governor_per_policy())\n\t\tglobal_tunables = NULL;\n}\n"
)
cleanup_new = (
    "static void sugov_clear_global_tunables(void)\n"
    "{\n\tif (!have_governor_per_policy())\n\t\tglobal_tunables = NULL;\n}\n"
)
if cleanup_old in text:
    text = text.replace(cleanup_old, cleanup_new, 1)
    rows.append("schedutil_global_cleanup=repaired\n")
else:
    rows.append("schedutil_global_cleanup=not-needed\n")
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
if exit_old in text:
    text = text.replace(exit_old, exit_new, 1)
    rows.append("schedutil_release_order=repaired\n")
else:
    rows.append("schedutil_release_order=not-needed\n")
path.write_text(text)

# Keep a single KDP-aware has_locked_children() helper. The 4.19.210 merge
# contains two copies with slightly different whitespace, so exact-string
# duplicate detection is insufficient.
path = root / "fs/namespace.c"
text = path.read_text()
plain_helper = (
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n\tstruct mount *child;\n\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n\t\t\tcontinue;\n\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n\t\t\treturn true;\n"
    "\t}\n\treturn false;\n}\n\n"
)
kdp_helper = (
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n\tstruct mount *child;\n\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n\t\t\tcontinue;\n\n"
    "#ifdef CONFIG_KDP_NS\n\t\tif (child->mnt->mnt_flags & MNT_LOCKED)\n"
    "#else\n\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n#endif\n"
    "\t\t\treturn true;\n\t}\n\treturn false;\n}\n\n"
)
if plain_helper in text:
    text = text.replace(plain_helper, kdp_helper, 1)
    rows.append("namespace_locked_children=repaired\n")
else:
    rows.append("namespace_locked_children=not-needed\n")

signature = "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"


def function_end(source: str, start: int) -> int:
    brace = source.find("{", start)
    if brace < 0:
        raise SystemExit("fs/namespace.c: has_locked_children opening brace missing")
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
    raise SystemExit("fs/namespace.c: has_locked_children closing brace missing")

starts = []
pos = 0
while True:
    pos = text.find(signature, pos)
    if pos < 0:
        break
    starts.append(pos)
    pos += len(signature)

if not starts:
    raise SystemExit("fs/namespace.c: has_locked_children helper is missing")
removed = 0
for start in reversed(starts[1:]):
    text = text[:start] + text[function_end(text, start):]
    removed += 1
rows.append(f"namespace_duplicate_helpers_removed={removed}\n")
if text.count(signature) != 1:
    raise SystemExit("fs/namespace.c: expected exactly one has_locked_children helper")
helper_start = text.index(signature)
helper_end = function_end(text, helper_start)
if "#ifdef CONFIG_KDP_NS" not in text[helper_start:helper_end]:
    raise SystemExit("fs/namespace.c: remaining has_locked_children helper is not KDP-aware")

clone_return = (
    "#ifdef CONFIG_KDP_NS\n\treturn new_mnt->mnt;\n#else\n\treturn &new_mnt->mnt;\n#endif\n"
    "}\nEXPORT_SYMBOL_GPL(clone_private_mount);\n"
)
if clone_return in text and "invalid:\n\tup_read(&namespace_sem);" not in text:
    text = text.replace(
        clone_return,
        "#ifdef CONFIG_KDP_NS\n\treturn new_mnt->mnt;\n#else\n\treturn &new_mnt->mnt;\n#endif\n\n"
        "invalid:\n\tup_read(&namespace_sem);\n\treturn ERR_PTR(-EINVAL);\n"
        "}\nEXPORT_SYMBOL_GPL(clone_private_mount);\n",
        1,
    )
    rows.append("clone_private_mount_invalid_exit=repaired\n")
else:
    rows.append("clone_private_mount_invalid_exit=not-needed\n")
path.write_text(text)

# Restore Samsung's intentionally disabled HCI bind stub.
path = root / "net/bluetooth/hci_sock.c"
text = path.read_text()
start_marker = "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n"
end_marker = "static int hci_sock_getname(struct socket *sock, struct sockaddr *addr,\n"
if start_marker in text and end_marker in text:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    region = text[start:end]
    if "hdev = hci_pi(sk)->hdev;" in region:
        text = text[:start] + (
            "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n"
            "\t\t\t int addr_len)\n{\n"
            "\t/* Binding is intentionally disabled by the Samsung vendor tree. */\n"
            "\treturn 0;\n}\n\n"
        ) + text[end:]
        rows.append("hci_sock_bind=repaired\n")
    else:
        rows.append("hci_sock_bind=not-needed\n")
else:
    rows.append("hci_sock_bind=anchors-missing\n")
path.write_text(text)

# Qualcomm event timer queue shape.
path = root / "drivers/soc/qcom/event_timer.c"
write_if_changed(
    path,
    "static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {\n\t.head = RB_ROOT,\n\t.next = NULL,\n};\n",
    "static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {\n\t.rb_root = RB_ROOT_CACHED,\n};\n",
    "event_timer_queue",
)

# USB address-zero serialization local state.
path = root / "drivers/usb/core/hub.c"
text = path.read_text()
anchor = "\tstatic int unreliable_port = -1;\n"
if "retry_locked" in text and "\tbool retry_locked;\n" not in text and anchor in text:
    text = text.replace(anchor, anchor + "\tbool retry_locked;\n", 1)
    rows.append("usb_hub_retry_locked=repaired\n")
else:
    rows.append("usb_hub_retry_locked=not-needed\n")
path.write_text(text)

# Preserve Samsung's previous-TRB fullness check.
path = root / "drivers/usb/dwc3/gadget.c"
text = path.read_text()
anchor = (
    "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
    "{\n\tu8\t\t\ttrbs_left;\n"
)
if "tmp = dwc3_ep_prev_trb(dep, dep->trb_enqueue);" in text and "struct dwc3_trb\t\t*tmp;" not in text and anchor in text:
    text = text.replace(
        anchor,
        "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
        "{\n\tstruct dwc3_trb\t\t*tmp;\n\tu8\t\t\ttrbs_left;\n",
        1,
    )
    rows.append("dwc3_trb_pointer=repaired\n")
else:
    rows.append("dwc3_trb_pointer=not-needed\n")
path.write_text(text)

report.write_text("".join(rows))
PY

git -C "$KERNEL_DIR" diff --check
cat "$REPORT"
info "Linux $TARGET_VERSION conditional compile repairs completed"
