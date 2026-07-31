#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/late-compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before late compile repair"

python3 - "$KERNEL_DIR" "$TOUCHGRASS_COMMIT" "$REPORT" <<'PY'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
touchgrass = sys.argv[2]
report = Path(sys.argv[3])
repairs = []


def git_blob(path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "show", f"{touchgrass}:{path}"],
        text=True,
    )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# The merged FUSE header contains the stable bad-inode helpers, while Samsung's
# extension state bit remains the final assigned value. Append FUSE_I_BAD after
# that vendor bit so existing state-bit numbering is preserved.
path = root / "fs/fuse/fuse_i.h"
text = path.read_text()
start = text.index("/** FUSE inode state bits */")
end = text.index("\n};", start)
segment = text[start:end]
if "\tFUSE_I_BAD," not in segment:
    anchor = (
        "\t/** Can be filled in by open, to use direct I/O on this file. */\n"
        "\tFUSE_I_ATTR_FORCE_SYNC,"
    )
    replacement = (
        anchor
        + "\n\t/** Inode is unusable after a protocol or I/O failure. */\n"
        + "\tFUSE_I_BAD,"
    )
    if segment.count(anchor) != 1:
        raise SystemExit(
            f"FUSE inode-state anchor mismatch: found {segment.count(anchor)}"
        )
    segment = segment.replace(anchor, replacement, 1)
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("fs/fuse/fuse_i.h=restored-fuse-i-bad-state-bit")

final = path.read_text()
start = final.index("/** FUSE inode state bits */")
end = final.index("\n};", start)
segment = final[start:end]
if segment.count("\tFUSE_I_BAD,") != 1:
    raise SystemExit("FUSE_I_BAD definition validation failed")
if segment.index("FUSE_I_BAD") < segment.index("FUSE_I_ATTR_FORCE_SYNC"):
    raise SystemExit("FUSE_I_BAD must not renumber the Samsung extension bit")
if "set_bit(FUSE_I_BAD" not in final or "test_bit(FUSE_I_BAD" not in final:
    raise SystemExit("FUSE bad-inode helpers are missing")


# task_mmu.c retains Samsung PAGE_BOOST and LMKD extensions. Restore the matching
# private procfs ABI, including pagevec definitions, then retain declarations
# required by the stable generic and inode implementations selected by the link
# closure.
proc_internal = root / "fs/proc/internal.h"
proc_text = git_blob("fs/proc/internal.h")
repairs.append("fs/proc/internal.h=restored-touchgrass-private-proc-abi")

old_fill_super = "extern int proc_fill_super(struct super_block *);\n"
new_fill_super = "extern int proc_fill_super(struct super_block *, void *, int);\n"
if old_fill_super in proc_text:
    if proc_text.count(old_fill_super) != 1:
        raise SystemExit("proc_fill_super declaration count mismatch")
    proc_text = proc_text.replace(old_fill_super, new_fill_super, 1)
elif proc_text.count(new_fill_super) != 1:
    raise SystemExit("proc_fill_super declaration is neither vendor nor stable-compatible")

net_ops_anchor = "extern const struct inode_operations proc_net_inode_operations;\n"
net_dentry_decl = "extern const struct dentry_operations proc_net_dentry_ops;\n"
if net_dentry_decl not in proc_text:
    if proc_text.count(net_ops_anchor) != 1:
        raise SystemExit("proc_net operations insertion anchor mismatch")
    proc_text = proc_text.replace(net_ops_anchor, net_ops_anchor + net_dentry_decl, 1)

generic_text = (root / "fs/proc/generic.c").read_text()
force_lookup = '''static inline void pde_force_lookup(struct proc_dir_entry *pde)
{
	/* /proc/net entries can change under setns(CLONE_NEWNET). */
	pde->proc_dops = &proc_net_dentry_ops;
}
'''
if "pde_force_lookup(" in generic_text and "static inline void pde_force_lookup(" not in proc_text:
    if proc_text.count(net_dentry_decl) != 1:
        raise SystemExit("proc_net dentry declaration insertion anchor mismatch")
    proc_text = proc_text.replace(net_dentry_decl, net_dentry_decl + force_lookup, 1)

proc_internal.write_text(proc_text)

proc_final = proc_internal.read_text()
for required in (
    "#define MAX_PAGE_BOOST_FILEPATH_LEN 256",
    "#include <linux/pagevec.h>",
    "struct proc_filemap_private {",
    "extern int proc_pid_statlmkd(",
    "extern const struct file_operations proc_reclaim_operations;",
    new_fill_super.strip(),
    net_dentry_decl.strip(),
):
    if required not in proc_final:
        raise SystemExit(f"touchGrass procfs ABI postcondition missing: {required}")
if "pde_force_lookup(" in generic_text and "static inline void pde_force_lookup(" not in proc_final:
    raise SystemExit("stable procfs force-lookup helper was not retained")

# Samsung validates mount options before calling the stable three-argument
# proc_fill_super(). Export its parser for inode.c and avoid parsing twice.
proc_root = root / "fs/proc/root.c"
root_text = proc_root.read_text()
old_parser = "static int proc_parse_options(char *options, struct pid_namespace *pid)\n"
new_parser = "int proc_parse_options(char *options, struct pid_namespace *pid)\n"
if old_parser in root_text:
    root_text = replace_once(root_text, old_parser, new_parser,
                             "proc option parser linkage")
    repairs.append("fs/proc/root.c=exported-proc-option-parser")
elif root_text.count(new_parser) != 1:
    raise SystemExit("proc_parse_options definition is neither old nor repaired")
old_fill_call = "\t\terr = proc_fill_super(sb);\n"
new_fill_call = "\t\terr = proc_fill_super(sb, NULL, 0);\n"
if old_fill_call in root_text:
    root_text = replace_once(root_text, old_fill_call, new_fill_call,
                             "proc_fill_super stable ABI call")
    repairs.append("fs/proc/root.c=called-stable-proc-fill-super-abi")
elif root_text.count(new_fill_call) != 1:
    raise SystemExit("proc_fill_super call is neither old nor repaired")
proc_root.write_text(root_text)

root_final = proc_root.read_text()
if root_final.count(new_parser) != 1 or old_parser in root_final:
    raise SystemExit("proc_parse_options linkage postcondition failed")
if root_final.count(new_fill_call) != 1 or old_fill_call in root_final:
    raise SystemExit("proc_fill_super call postcondition failed")
if "if (!proc_parse_options(options, ns))" not in root_final:
    raise SystemExit("Samsung proc mount option validation was lost")


# The Samsung io-pgtable source uses public definitions from
# include/linux/io-pgtable.h. Neither merge parent provides a local
# drivers/iommu/io-pgtable.h, so point the source to the public header.
io_arm = root / "drivers/iommu/io-pgtable-arm.c"
io_text = io_arm.read_text()
old_include = '#include "io-pgtable.h"\n'
new_include = '#include <linux/io-pgtable.h>\n'
if old_include in io_text:
    if io_text.count(old_include) != 1:
        raise SystemExit("unexpected local io-pgtable include count")
    io_text = io_text.replace(old_include, new_include, 1)
    io_arm.write_text(io_text)
    repairs.append("drivers/iommu/io-pgtable-arm.c=used-public-page-table-header")
elif io_text.count(new_include) != 1:
    raise SystemExit("IOMMU page-table include is neither local nor repaired")

io_final = io_arm.read_text()
if old_include in io_final or io_final.count(new_include) != 1:
    raise SystemExit("public IOMMU page-table include repair failed")
if not (root / "include/linux/io-pgtable.h").is_file():
    raise SystemExit("public Linux IOMMU page-table header is missing")


# snd_pcm_hw_free() takes the runtime buffer-access lock and retains an error
# jump to unlock, but the merge dropped the matching label and unlock call.
pcm = root / "sound/core/pcm_native.c"
pcm_text = pcm.read_text()
start = pcm_text.index("static int snd_pcm_hw_free(struct snd_pcm_substream *substream)")
end = pcm_text.index("\nstatic int snd_pcm_sw_params", start)
segment = pcm_text[start:end]
if "snd_pcm_buffer_access_unlock(runtime);" not in segment:
    old = (
        "\tif (pm_qos_request_active(&substream->latency_pm_qos_req))\n"
        "\t\tpm_qos_remove_request(&substream->latency_pm_qos_req);\n"
        "\treturn result;\n"
    )
    new = (
        "\tif (pm_qos_request_active(&substream->latency_pm_qos_req))\n"
        "\t\tpm_qos_remove_request(&substream->latency_pm_qos_req);\n"
        " unlock:\n"
        "\tsnd_pcm_buffer_access_unlock(runtime);\n"
        "\treturn result;\n"
    )
    segment = replace_once(segment, old, new, "PCM hw-free unlock")
    pcm_text = pcm_text[:start] + segment + pcm_text[end:]
    pcm.write_text(pcm_text)
    repairs.append("sound/core/pcm_native.c=restored-buffer-access-unlock")

pcm_final = pcm.read_text()
start = pcm_final.index("static int snd_pcm_hw_free(struct snd_pcm_substream *substream)")
end = pcm_final.index("\nstatic int snd_pcm_sw_params", start)
segment = pcm_final[start:end]
if segment.count("goto unlock;") != 1 or segment.count(" unlock:") != 1:
    raise SystemExit("PCM hw-free unlock label validation failed")
if segment.count("snd_pcm_buffer_access_unlock(runtime);") != 1:
    raise SystemExit("PCM buffer unlock validation failed")


# The vendor mailbox queue split retained irqsave operations around the poll
# hrtimer lock, but the direct merge dropped their local flags variable.
mailbox = root / "drivers/mailbox/mailbox.c"
mail_text = mailbox.read_text()
submit_start = mail_text.index("static void msg_submit(struct mbox_chan *chan)")
submit_end = mail_text.index("static void tx_tick", submit_start)
submit = mail_text[submit_start:submit_end]
if "\tunsigned long flags;\n" not in submit:
    old = (
        "static void msg_submit(struct mbox_chan *chan)\n"
        "{\n"
        "\tint err = 0;\n"
    )
    new = (
        "static void msg_submit(struct mbox_chan *chan)\n"
        "{\n"
        "\tunsigned long flags;\n"
        "\tint err = 0;\n"
    )
    mail_text = replace_once(mail_text, old, new,
                             "mailbox poll timer irq flags")
    mailbox.write_text(mail_text)
    repairs.append("drivers/mailbox/mailbox.c=declared-poll-hrtimer-irq-flags")

mail_final = mailbox.read_text()
submit_start = mail_final.index("static void msg_submit(struct mbox_chan *chan)")
submit_end = mail_final.index("static void tx_tick", submit_start)
submit = mail_final[submit_start:submit_end]
if submit.count("unsigned long flags;") != 1:
    raise SystemExit("msg_submit flags declaration validation failed")
if "spin_lock_irqsave(&chan->mbox->poll_hrt_lock, flags);" not in submit:
    raise SystemExit("msg_submit poll timer lock is missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  fs/fuse/fuse_i.h fs/proc/internal.h fs/proc/root.c \
  drivers/iommu/io-pgtable-arm.c sound/core/pcm_native.c \
  drivers/mailbox/mailbox.c
info "Linux $TARGET_VERSION late compile mismatches repaired"
