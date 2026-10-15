#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()


def read(rel):
    return (root / rel).read_text()


def write(rel, text):
    (root / rel).write_text(text)


def replace_once(rel, old, new, label):
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {rel}, found {count}")
    write(rel, text.replace(old, new, 1))
    print(f"applied={label}")


def replace_first(rel, old, new, label):
    text = read(rel)
    count = text.count(old)
    if count < 1:
        raise SystemExit(f"{label}: expected at least one match in {rel}, found {count}")
    write(rel, text.replace(old, new, 1))
    print(f"applied={label}")


def require(rel, needle, label):
    if needle not in read(rel):
        raise SystemExit(f"{label}: missing in {rel}: {needle!r}")


def require_absent(rel, needle, label):
    if needle in read(rel):
        raise SystemExit(f"{label}: unexpected in {rel}: {needle!r}")


def remove_reject(rel, label):
    reject = root / (rel + ".rej")
    if not reject.exists():
        raise SystemExit(f"{label}: reject is missing: {reject}")
    reject.unlink()
    print(f"accepted={label}")


def auto_apply(rel):
    reject = root / (rel + ".rej")
    lines = reject.read_text().splitlines()
    patch_text = f"--- a/{rel}\n+++ b/{rel}\n" + "\n".join(lines[1:]) + "\n"
    result = subprocess.run(
        [
            "patch", "--batch", "--forward", "--no-backup-if-mismatch",
            "--fuzz=3", "-p1",
        ],
        cwd=root,
        input=patch_text.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout.decode(errors="replace"), end="")
    if result.returncode != 0:
        raise SystemExit(f"auto patch failed: {rel}")
    reject.unlink()
    print(f"applied=fuzzy-context:{rel}")


for rel in [
    "drivers/hid/hid-ids.h",
    "drivers/mmc/core/bus.c",
    "drivers/mmc/core/core.h",
    "drivers/mmc/core/mmc.c",
    "drivers/mmc/core/sd.c",
    "drivers/usb/core/hub.c",
    "drivers/usb/dwc3/debug.h",
    "fs/ext4/inode.c",
    "fs/ext4/super.c",
    "include/asm-generic/vmlinux.lds.h",
    "include/linux/mm.h",
    "include/linux/netdevice.h",
    "include/uapi/linux/bpf.h",
    "kernel/cgroup/cgroup.c",
    "kernel/cpu.c",
    "kernel/locking/lockdep.c",
    "kernel/sched/fair.c",
    "kernel/trace/bpf_trace.c",
    "kernel/workqueue.c",
    "mm/memory.c",
    "mm/rmap.c",
    "net/ipv4/udp.c",
    "net/ipv4/udp_offload.c",
    "net/ipv6/tcp_ipv6.c",
    "net/mac802154/llsec.c",
    "sound/usb/card.c",
    "sound/usb/usbaudio.h",
]:
    auto_apply(rel)

replace_once(
    "arch/arm64/include/asm/alternative.h",
    """663:\t\\insn2
664:\t.popsection
\t.org\t. - (664b-663b) + (662b-661b)
\t.org\t. - (662b-661b) + (664b-663b)
""",
    """663:\t\\insn2
664:\t.org\t. - (664b-663b) + (662b-661b)
\t.org\t. - (662b-661b) + (664b-663b)
\t.popsection
""",
    "arm64 alternative section size checks",
)
replace_once(
    "arch/arm64/include/asm/alternative.h",
    """664:
\t.if .Lasm_alt_mode==0
\t.popsection
\t.endif
\t.org\t. - (664b-663b) + (662b-661b)
\t.org\t. - (662b-661b) + (664b-663b)
""",
    """664:
\t.org\t. - (664b-663b) + (662b-661b)
\t.org\t. - (662b-661b) + (664b-663b)
\t.if .Lasm_alt_mode==0
\t.popsection
\t.endif
""",
    "arm64 alternative endif ordering",
)
remove_reject("arch/arm64/include/asm/alternative.h", "reviewed ARM64 alternatives")

require_absent("arch/x86/entry/vdso/vma.c", "vgetcpu_cpu_init", "x86 vendor VDSO layout")
remove_reject("arch/x86/entry/vdso/vma.c", "retained unrelated x86 vendor VDSO")

require("drivers/mmc/core/core.c", "if (err)\n\t\tgoto power_cycle;", "MMC UHS power-cycle fallback")
remove_reject("drivers/mmc/core/core.c", "MMC UHS fallback already present")

replace_once(
    "drivers/mmc/core/mmc_ops.c",
    """\tif (mmc_card_mmc(card) &&
\t\t\t(card->ext_csd.cache_size > 0) &&
\t\t\t(card->ext_csd.cache_ctrl & 1) &&
\t\t\t(!(card->quirks & MMC_QUIRK_CACHE_DISABLE))) {
""",
    """\tif (mmc_cache_enabled(card->host) &&
\t    !(card->quirks & MMC_QUIRK_CACHE_DISABLE)) {
""",
    "MMC cache flush host-state conversion",
)
remove_reject("drivers/mmc/core/mmc_ops.c", "reviewed MMC cache conversion")

require("drivers/thermal/thermal_sysfs.c", "if (!stats)\n\t\treturn;", "thermal stats null guard")
remove_reject("drivers/thermal/thermal_sysfs.c", "thermal null guard already present")

require("drivers/usb/dwc3/core.c", "ret = dwc3_gadget_init(dwc);", "vendor DWC3 gadget initialization")
require("drivers/usb/dwc3/core.c", "dwc3_debugfs_init(dwc);\n\treturn 0;", "vendor DWC3 debugfs ordering")
require("drivers/usb/dwc3/core.c", "dwc3_debugfs_exit(dwc);\n\tdwc3_gadget_exit(dwc);", "vendor DWC3 teardown ordering")
remove_reject("drivers/usb/dwc3/core.c", "retained Qualcomm DWC3 lifecycle")

require("drivers/usb/dwc3/debugfs.c", "dwc3_debugfs_create_endpoint_dirs(dwc, root);", "vendor DWC3 endpoint debugfs creation")
remove_reject("drivers/usb/dwc3/debugfs.c", "retained Qualcomm DWC3 debugfs topology")

require("drivers/usb/dwc3/gadget.c", "__dwc3_gadget_kick_transfer(dep);\n\n\treturn 0;", "DWC3 queue kick return semantics")
require("drivers/usb/dwc3/gadget.c", "reg |= DWC3_DEVTEN_EOPFEN;", "DWC3 suspend-event interrupt")
replace_once(
    "drivers/usb/dwc3/gadget.c",
    "\tif (req->num_pending_sgs)\n\t\tret = dwc3_gadget_ep_reclaim_trb_sg(dep, req, event,\n",
    "\tif (req->request.num_mapped_sgs)\n\t\tret = dwc3_gadget_ep_reclaim_trb_sg(dep, req, event,\n",
    "DWC3 mapped SG completion detection",
)
remove_reject("drivers/usb/dwc3/gadget.c", "reviewed Qualcomm DWC3 gadget adaptations")

for field in ["fs_descriptors", "hs_descriptors", "ss_descriptors", "ssp_descriptors"]:
    require("drivers/usb/gadget/config.c", f"f->{field} = NULL;", f"USB descriptor clear {field}")
remove_reject("drivers/usb/gadget/config.c", "USB descriptor clears already present")

for needle in [
    "#define MAX_USB_STRING_LEN\t126",
    "#define MAX_USB_STRING_WITH_NULL_LEN\t(MAX_USB_STRING_LEN+1)",
    "str = kmalloc(MAX_USB_STRING_WITH_NULL_LEN, GFP_KERNEL);",
    "strlcpy(str, s, MAX_USB_STRING_WITH_NULL_LEN);",
]:
    require("drivers/usb/gadget/configfs.c", needle, "bounded USB configfs string copy")
remove_reject("drivers/usb/gadget/configfs.c", "retained stronger vendor USB string copy")

replace_once(
    "drivers/usb/gadget/function/f_fs.c",
    """\tffs_dev = ffs_acquire_dev(dev_name);
\tif (IS_ERR(ffs_dev)) {
\t\tffs_data_put(ffs);
\t\treturn ERR_CAST(ffs_dev);
\t}
\tffs->private_data = ffs_dev;
\tdata.ffs_data = ffs;

\trv = mount_nodev(t, flags, &data, ffs_sb_fill);
\tif (IS_ERR(rv) && data.ffs_data) {
\t\tffs_release_dev(data.ffs_data);
\t\tffs_data_put(data.ffs_data);
\t}
""",
    """\tret = ffs_acquire_dev(dev_name, ffs);
\tif (ret) {
\t\tffs_data_put(ffs);
\t\treturn ERR_PTR(ret);
\t}
\tdata.ffs_data = ffs;

\trv = mount_nodev(t, flags, &data, ffs_sb_fill);
\tif (IS_ERR(rv) && data.ffs_data)
\t\tffs_data_put(data.ffs_data);
""",
    "FunctionFS mount ownership",
)
replace_once(
    "drivers/usb/gadget/function/f_fs.c",
    """\tstruct f_fs_opts *ffs_opts =
\t\tcontainer_of(f->fi, struct f_fs_opts, func_inst);
\tstruct ffs_data *ffs = ffs_opts->dev->ffs_data;
\tint ret;
""",
    """\tstruct f_fs_opts *ffs_opts =
\t\tcontainer_of(f->fi, struct f_fs_opts, func_inst);
\tstruct ffs_data *ffs_data;
\tint ret;
""",
    "FunctionFS bind data declaration",
)
replace_once(
    "drivers/usb/gadget/function/f_fs.c",
    """\t/* Clear the private_data pointer to stop incorrect dev access */
\tif (dev->ffs_data)
\t\tdev->ffs_data->private_data = NULL;

""",
    "",
    "FunctionFS centralized device release",
)
replace_once(
    "drivers/usb/gadget/function/f_fs.c",
    """static void *ffs_acquire_dev(const char *dev_name)
{
\tstruct ffs_dev *ffs_dev;

\tENTER();

\tffs_dev_lock();

\tffs_dev = _ffs_find_dev(dev_name);
\tif (!ffs_dev)
\t\tffs_dev = ERR_PTR(-ENOENT);
\telse if (ffs_dev->mounted)
\t\tffs_dev = ERR_PTR(-EBUSY);
\telse if (ffs_dev->ffs_acquire_dev_callback &&
\t    ffs_dev->ffs_acquire_dev_callback(ffs_dev))
\t\tffs_dev = ERR_PTR(-ENOENT);
\telse
\t\tffs_dev->mounted = true;

\tffs_dev_unlock();

\treturn ffs_dev;
}

static void ffs_release_dev(struct ffs_data *ffs_data)
{
\tstruct ffs_dev *ffs_dev;

\tENTER();

\tffs_dev_lock();

\tffs_dev = ffs_data->private_data;
\tif (ffs_dev) {
\t\tffs_dev->mounted = false;

\t\tif (ffs_dev->ffs_release_dev_callback)
\t\t\tffs_dev->ffs_release_dev_callback(ffs_dev);
\t}

\tffs_dev_unlock();
}
""",
    """static int ffs_acquire_dev(const char *dev_name, struct ffs_data *ffs_data)
{
\tint ret = 0;
\tstruct ffs_dev *ffs_dev;

\tENTER();
\tffs_dev_lock();

\tffs_dev = _ffs_find_dev(dev_name);
\tif (!ffs_dev) {
\t\tret = -ENOENT;
\t} else if (ffs_dev->mounted) {
\t\tret = -EBUSY;
\t} else if (ffs_dev->ffs_acquire_dev_callback &&
\t\t   ffs_dev->ffs_acquire_dev_callback(ffs_dev)) {
\t\tret = -ENOENT;
\t} else {
\t\tffs_dev->mounted = true;
\t\tffs_dev->ffs_data = ffs_data;
\t\tffs_data->private_data = ffs_dev;
\t}

\tffs_dev_unlock();
\treturn ret;
}

static void ffs_release_dev(struct ffs_dev *ffs_dev)
{
\tENTER();
\tffs_dev_lock();

\tif (ffs_dev && ffs_dev->mounted) {
\t\tffs_dev->mounted = false;
\t\tif (ffs_dev->ffs_data) {
\t\t\tffs_dev->ffs_data->private_data = NULL;
\t\t\tffs_dev->ffs_data = NULL;
\t\t}

\t\tif (ffs_dev->ffs_release_dev_callback)
\t\t\tffs_dev->ffs_release_dev_callback(ffs_dev);
\t}

\tffs_dev_unlock();
}
""",
    "FunctionFS device ownership definitions",
)
for needle in [
    "static int ffs_acquire_dev(const char *dev_name, struct ffs_data *ffs_data);",
    "static void ffs_release_dev(struct ffs_dev *ffs_dev);",
    "ffs_release_dev(ffs->private_data);",
    "ffs_release_dev(opts->dev);",
]:
    require("drivers/usb/gadget/function/f_fs.c", needle, "FunctionFS ownership postcondition")
require_absent("drivers/usb/gadget/function/f_fs.c", "static void *ffs_acquire_dev", "old FunctionFS acquire API")
remove_reject("drivers/usb/gadget/function/f_fs.c", "reviewed FunctionFS ownership conversion")

require("drivers/usb/gadget/function/f_ncm.c", "static struct sk_buff *ncm_wrap_ntb", "vendor NCM wrapper")
require_absent("drivers/usb/gadget/function/f_ncm.c", "task_timer", "vendor NCM aggregation timer")
remove_reject("drivers/usb/gadget/function/f_ncm.c", "retained Samsung non-aggregating NCM wrapper")

replace_once(
    "fs/crypto/fname.c",
    """\tif (hash) {
\t\tnokey_name.dirhash[0] = hash;
\t\tnokey_name.dirhash[1] = minor_hash;
\t} else {
\t\tnokey_name.dirhash[0] = 0;
\t\tnokey_name.dirhash[1] = 0;
\t}
""",
    """\tnokey_name.dirhash[0] = hash;
\tnokey_name.dirhash[1] = minor_hash;
""",
    "fscrypt no-key dirhash initialization",
)
remove_reject("fs/crypto/fname.c", "reviewed fscrypt no-key dirhash initialization")

replace_once(
    "fs/ext4/extents.c",
    """\tif (!ext4_has_feature_journal(inode->i_sb) ||
\t    (inode->i_ino !=
\t     le32_to_cpu(EXT4_SB(inode->i_sb)->s_es->s_journal_inum))) {
\t\terr = __ext4_ext_check(function, line, inode,
\t\t\t\t       ext_block_hdr(bh), depth, pblk, bh);
\t\tif (err)
\t\t\tgoto errout;
\t}
""",
    """\terr = __ext4_ext_check(function, line, inode,
\t\t\t       ext_block_hdr(bh), depth, pblk, bh);
\tif (err)
\t\tgoto errout;
""",
    "ext4 unconditional extent-block validation",
)
for needle in ["eh->eh_generation = 0;", "neh->eh_generation = 0;"]:
    require("fs/ext4/extents.c", needle, "ext4 extent generation initialization")
remove_reject("fs/ext4/extents.c", "reviewed ext4 extent validation")

replace_first(
    "fs/ext4/namei.c",
    """\tretval = -ENOENT;
\tif (!old.bh || le32_to_cpu(old.de->inode) != old.inode->i_ino)
\t\tgoto end_rename;

\tnew.bh = ext4_find_entry(new.dir, &new.dentry->d_name,
\t\t\t\t &new.de, &new.inlined, NULL);
\tif (IS_ERR(new.bh)) {
\t\tretval = PTR_ERR(new.bh);
\t\tnew.bh = NULL;
\t\tgoto end_rename;
\t}
""",
    """\tretval = -ENOENT;
\tif (!old.bh || le32_to_cpu(old.de->inode) != old.inode->i_ino)
\t\tgoto release_bh;

\tnew.bh = ext4_find_entry(new.dir, &new.dentry->d_name,
\t\t\t\t &new.de, &new.inlined, NULL);
\tif (IS_ERR(new.bh)) {
\t\tretval = PTR_ERR(new.bh);
\t\tnew.bh = NULL;
\t\tgoto release_bh;
\t}
""",
    "ext4 rename pre-journal error path",
)
remove_reject("fs/ext4/namei.c", "reviewed ext4 rename cleanup")

require("fs/seq_file.c", "if (unlikely(size > MAX_RW_COUNT))\n\t\treturn NULL;", "seq_file allocation bound")
remove_reject("fs/seq_file.c", "seq_file bound already present")

require("include/net/af_unix.h", "void unix_destruct_scm(struct sk_buff *skb);", "UNIX SCM destructor declaration")
remove_reject("include/net/af_unix.h", "UNIX SCM declaration already present")
require("include/net/sctp/structs.h", "bool\t\t(*from_addr_param)", "SCTP checked address conversion signature")
remove_reject("include/net/sctp/structs.h", "SCTP bool conversion already present")

replace_once(
    "kernel/bpf/verifier.c",
    """\tif (ptr_reg->type == PTR_TO_MAP_VALUE &&
\t    !env->allow_ptr_leaks && !known && (smin_val < 0) != (smax_val < 0)) {
\t\tverbose(env, "R%d has unknown scalar with mixed signed bounds, pointer arithmetic with it prohibited for !root\\n",
\t\t\toff_reg == dst_reg ? dst : src);
\t\treturn -EACCES;
\t}

""",
    "",
    "BPF duplicate mixed-signed-bound check",
)
remove_reject("kernel/bpf/verifier.c", "reviewed BPF verifier bound check")

replace_once(
    "kernel/locking/mutex.c",
    """\tstruct ww_mutex *ww;
\tint ret;

\tmight_sleep();
""",
    """\tstruct ww_mutex *ww;
\tint ret;

\tif (!use_ww_ctx)
\t\tww_ctx = NULL;

\tmight_sleep();
""",
    "WW mutex context normalization",
)
replace_once(
    "kernel/locking/mutex.c",
    "\tif (use_ww_ctx && ww_ctx) {\n",
    "\tif (ww_ctx) {\n",
    "WW mutex context condition",
)
remove_reject("kernel/locking/mutex.c", "reviewed WW mutex context handling")

require("net/Makefile", "obj-$(CONFIG_UNIX_SCM)\t\t+= unix/", "UNIX SCM top-level build gate")
remove_reject("net/Makefile", "UNIX SCM top-level build gate already present")

replace_once(
    "net/core/filter.c",
    """static u32 __bpf_skb_max_len(const struct sk_buff *skb)
{
\tif (skb_at_tc_ingress(skb) || !skb->dev)
\t\treturn SKB_MAX_ALLOC;
\treturn skb->dev->mtu + skb->dev->hard_header_len;
}
""",
    "#define BPF_SKB_MAX_LEN SKB_MAX_ALLOC\n",
    "BPF skb allocation ceiling",
)
replace_once(
    "net/core/filter.c",
    "\tu32 len_max = __bpf_skb_max_len(skb);\n",
    "\tu32 len_max = BPF_SKB_MAX_LEN;\n",
    "BPF skb adjustment ceiling use",
)
remove_reject("net/core/filter.c", "reviewed BPF skb size ceiling")

replace_once(
    "net/ipv4/tcp_ipv4.c",
    """\t\trx_queue = max_t(int, READ_ONCE(tp->rcv_nxt) -
\t\t\t\t      tp->copied_seq, 0);
""",
    """\t\trx_queue = max_t(int, READ_ONCE(tp->rcv_nxt) -
\t\t\t\t      READ_ONCE(tp->copied_seq), 0);
""",
    "TCP copied sequence lockless read",
)
replace_once(
    "net/ipv4/tcp_ipv4.c",
    "\t\ttp->write_seq - tp->snd_una,\n",
    "\t\tREAD_ONCE(tp->write_seq) - tp->snd_una,\n",
    "TCP write sequence lockless read",
)
remove_reject("net/ipv4/tcp_ipv4.c", "reviewed TCP lockless sequence reads")

replace_once(
    "net/qrtr/qrtr.c",
    "\tunsigned int size;\n\tint errcode;\n",
    "\tsize_t size;\n\tint errcode;\n",
    "QRTR packet length type",
)
require("net/qrtr/qrtr.c", "alloc_skb_with_frags(sizeof(*v1), len, 0, &errcode, GFP_ATOMIC);", "Qualcomm QRTR atomic fragment allocator")
require("net/qrtr/qrtr.c", "memset(addr, 0, sizeof(*addr));", "QRTR sockaddr hole clearing")
remove_reject("net/qrtr/qrtr.c", "reviewed Qualcomm QRTR adaptations")

sctp_checks = {
    "net/sctp/bind_addr.c": ["!af->from_addr_param(&addr, rawaddr, htons(port), 0)", "goto out_err;"],
    "net/sctp/input.c": ["if (!af->from_addr_param(paddr, params.addr, sh->source, 0))", "if (!af->from_addr_param(&paddr, param, peer_port, 0))", "ch_end + sizeof(*ch) < skb_tail_pointer(skb)"],
    "net/sctp/ipv6.c": ["static bool sctp_v6_from_addr_param", "return false;", "return true;"],
    "net/sctp/protocol.c": ["static bool sctp_v4_from_addr_param", "return false;", "return true;"],
    "net/sctp/sm_make_chunk.c": ["if (!af->from_addr_param(&addr, param.addr", "if (!af->from_addr_param(&addr, addr_param"],
}
for rel, needles in sctp_checks.items():
    for needle in needles:
        require(rel, needle, "SCTP checked address conversion")
    remove_reject(rel, "SCTP checked conversion already present")

for rel, needles in {
    "net/unix/Kconfig": ["config UNIX_SCM", "depends on UNIX", "default y"],
    "net/unix/Makefile": ["obj-$(CONFIG_UNIX_SCM)\t+= scm.o"],
    "net/unix/af_unix.c": ["#include \"scm.h\"", "static void unix_peek_fds", "unix_attach_fds(scm, skb)", "skb->destructor = unix_destruct_scm"],
    "net/unix/garbage.c": ["#include \"scm.h\"", "gc_candidates", "unix_gc_lock"],
}.items():
    for needle in needles:
        require(rel, needle, "UNIX SCM split")
    remove_reject(rel, "UNIX SCM split already present")
require_absent("net/unix/af_unix.c", "static void unix_detach_fds", "old UNIX SCM implementation")
require_absent("net/unix/garbage.c", "void unix_inflight(struct", "old UNIX inflight implementation")
for rel in ["net/unix/scm.c", "net/unix/scm.h"]:
    if not (root / rel).is_file():
        raise SystemExit(f"UNIX SCM split file is missing: {rel}")
require("net/unix/scm.c", "void unix_destruct_scm", "new UNIX SCM destructor")
require("net/unix/scm.c", "void unix_inflight", "new UNIX inflight accounting")

for needle in [
    "int max_key_idx = 5;",
    "NL80211_EXT_FEATURE_BEACON_PROTECTION",
    "max_key_idx = 7;",
    "if (key_idx < 0 || key_idx > max_key_idx)",
]:
    require("net/wireless/util.c", needle, "vendor cfg80211 key-index validation")
remove_reject("net/wireless/util.c", "retained newer cfg80211 key-index policy")

if read("security/selinux/avc.c").count("GFP_NOWAIT | __GFP_NOWARN") < 5:
    raise SystemExit("SELinux nonblocking allocations are missing __GFP_NOWARN")
remove_reject("security/selinux/avc.c", "SELinux allocation flags already present")

lpfc = root / "drivers/scsi/lpfc/lpfc_mbox.c"
if lpfc.is_file():
    text = lpfc.read_text()
    normalized = text.rstrip("\n") + "\n"
    if normalized != text:
        lpfc.write_text(normalized)
        print("applied=normalize LPFC final newline")

remaining = sorted(str(path.relative_to(root)) for path in root.rglob("*.rej"))
if remaining:
    raise SystemExit("remaining rejects: " + ", ".join(remaining))

final_checks = {
    "arch/arm64/include/asm/alternative.h": ["664:\t.org\t. - (664b-663b) + (662b-661b)"],
    "drivers/mmc/core/mmc_ops.c": ["mmc_cache_enabled(card->host)", "MMC_QUIRK_CACHE_DISABLE"],
    "drivers/usb/gadget/function/f_fs.c": ["ffs_acquire_dev(dev_name, ffs)", "ffs_dev->ffs_data = ffs_data", "ffs_data->private_data = ffs_dev"],
    "fs/ext4/extents.c": ["ext_block_hdr(bh), depth, pblk, bh);"],
    "fs/ext4/namei.c": ["goto release_bh;"],
    "kernel/locking/mutex.c": ["if (!use_ww_ctx)", "if (ww_ctx) {"],
    "net/core/filter.c": ["#define BPF_SKB_MAX_LEN SKB_MAX_ALLOC", "u32 len_max = BPF_SKB_MAX_LEN;"],
    "net/ipv4/tcp_ipv4.c": ["READ_ONCE(tp->copied_seq)", "READ_ONCE(tp->write_seq)"],
    "net/qrtr/qrtr.c": ["size_t size;", "alloc_skb_with_frags"],
}
for rel, needles in final_checks.items():
    for needle in needles:
        require(rel, needle, "final Linux 4.19.200 review")

print("result=reviewed-linux-4.19.200-resolver-complete")
