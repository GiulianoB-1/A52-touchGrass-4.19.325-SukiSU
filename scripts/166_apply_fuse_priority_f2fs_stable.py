#!/usr/bin/env python3
from pathlib import Path
import sys

P="P166"

def die(msg):
    raise SystemExit(f"{P}: {msg}")

def rd(root, rel):
    p=root/rel
    if not p.is_file():
        die(f"missing {rel}")
    return p.read_text()

def wr(root, rel, text):
    (root/rel).write_text(text)

def rep1(text, old, new, label):
    n=text.count(old)
    if n!=1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old,new,1)

def span(text, sig):
    a=text.find(sig)
    if a<0:
        die(f"missing function {sig}")
    b=text.find("{",a)
    if b<0:
        die(f"missing opening brace {sig}")
    depth=0
    for i in range(b,len(text)):
        if text[i]=="{": depth+=1
        elif text[i]=="}":
            depth-=1
            if depth==0:
                return a,i+1
    die(f"unterminated function {sig}")

def fun(text,sig):
    a,b=span(text,sig)
    return text[a:b]

def patch_fuse(root,out):
    rel="fs/fuse/inode.c"
    s=rd(root,rel)

    # P165 deliberately restored the known-good Phase104 offer: passthrough,
    # no WRITEBACK_CACHE. P166 re-offers writeback as an experiment.
    no_wb="""\t\tFUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |
\t\tFUSE_NO_OPEN_SUPPORT |
"""
    with_wb="""\t\tFUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |
\t\tFUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT |
"""
    if no_wb in s:
        s=rep1(s,no_wb,with_wb,"FUSE writeback offer")
        out.append("FUSE_WRITEBACK_OFFER=restored")
    elif with_wb in s:
        out.append("FUSE_WRITEBACK_OFFER=already_present")
    else:
        die("FUSE capability list shape unknown")

    init_sig="static void process_init_reply(struct fuse_conn *fc, struct fuse_req *req)"
    a,b=span(s,init_sig)
    block=s[a:b]

    legacy_start=block.find("if (arg->minor < 36 && (arg->flags & FUSE_PASSTHROUGH)) {")
    modern_start=block.find("} else if (arg->minor >= 40 &&",legacy_start)
    if legacy_start<0 or modern_start<0:
        die("FUSE legacy/modern passthrough branches not found")
    legacy_before=block[legacy_start:modern_start]

    old_tail="""\t\t\t\t   arg->max_stack_depth > 0 &&
\t\t\t\t   arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH &&
\t\t\t\t   !(arg->flags & FUSE_WRITEBACK_CACHE)) {
\t\t\t\tfc->passthrough = 1;
"""
    new_tail="""\t\t\t\t   arg->max_stack_depth > 0 &&
\t\t\t\t   arg->max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH) {
\t\t\t\t/*
\t\t\t\t * P166 experiment: Android MediaProvider can request both
\t\t\t\t * upstream passthrough and WRITEBACK_CACHE. Prefer passthrough
\t\t\t\t * for this connection and leave writeback enabled for daemons
\t\t\t\t * that do not negotiate passthrough.
\t\t\t\t */
\t\t\t\tfc->writeback_cache = 0;
\t\t\t\tfc->passthrough = 1;
"""
    if old_tail in block:
        block=rep1(block,old_tail,new_tail,"FUSE modern PT priority")
        out.append("FUSE_PT_PRIORITY=patched")
    elif "P166 experiment: Android MediaProvider" in block:
        out.append("FUSE_PT_PRIORITY=already_present")
    else:
        die("FUSE modern passthrough condition shape unknown")

    legacy_after=block[legacy_start:block.find("} else if (arg->minor >= 40 &&",legacy_start)]
    if legacy_after != legacy_before:
        die("legacy minor<36 passthrough branch changed")
    if "!(arg->flags & FUSE_WRITEBACK_CACHE)" in block[block.find("} else if (arg->minor >= 40 &&"):]:
        die("modern passthrough still excludes WRITEBACK_CACHE")
    if "fc->writeback_cache = 0;" not in block[block.find("} else if (arg->minor >= 40 &&"):]:
        die("modern passthrough lacks writeback override")

    s=s[:a]+block+s[b:]
    wr(root,rel,s)
    out.append("FUSE_LEGACY_PT=unchanged")

def patch_f2fs_acl_checkpoint(root,out):
    # 4d9c9b7: make chmod ACL/mode update atomic.
    rel="fs/f2fs/acl.c"
    s=rd(root,rel)
    helper="""static int f2fs_acl_update_mode(struct inode *inode, umode_t *mode_p,
\t\t\t  struct posix_acl **acl)
{
\tumode_t mode = inode->i_mode;
\tint error;

\tif (is_inode_flag_set(inode, FI_ACL_MODE))
\t\tmode = F2FS_I(inode)->i_acl_mode;

\terror = posix_acl_equiv_mode(*acl, &mode);
\tif (error < 0)
\t\treturn error;
\tif (error == 0)
\t\t*acl = NULL;
\tif (!in_group_p(inode->i_gid) &&
\t    !capable_wrt_inode_uidgid(inode, CAP_FSETID))
\t\tmode &= ~S_ISGID;
\t*mode_p = mode;
\treturn 0;
}

"""
    if "static int f2fs_acl_update_mode(" not in s:
        anchor="""struct posix_acl *f2fs_get_acl(struct inode *inode, int type)
{
\treturn __f2fs_get_acl(inode, type, NULL);
}

"""
        s=rep1(s,anchor,anchor+helper,"F2FS ACL helper insertion")
        out.append("F2FS_4D9_ACL_HELPER=patched")
    else:
        out.append("F2FS_4D9_ACL_HELPER=present")
    s=s.replace("error = posix_acl_update_mode(inode, &mode, &acl);",
                "error = f2fs_acl_update_mode(inode, &mode, &acl);",1)
    if "error = f2fs_acl_update_mode(inode, &mode, &acl);" not in s:
        die("F2FS ACL helper call missing")
    wr(root,rel,s)

    rel="fs/f2fs/file.c"
    s=rd(root,rel)
    old="""\tif (attr->ia_valid & ATTR_MODE) {
\t\terr = posix_acl_chmod(inode, f2fs_get_inode_mode(inode));
\t\tif (err || is_inode_flag_set(inode, FI_ACL_MODE)) {
\t\t\tinode->i_mode = F2FS_I(inode)->i_acl_mode;
\t\t\tclear_inode_flag(inode, FI_ACL_MODE);
\t\t}
\t}
"""
    new="""\tif (attr->ia_valid & ATTR_MODE) {
\t\terr = posix_acl_chmod(inode, f2fs_get_inode_mode(inode));

\t\tif (is_inode_flag_set(inode, FI_ACL_MODE)) {
\t\t\tif (!err)
\t\t\t\tinode->i_mode = F2FS_I(inode)->i_acl_mode;
\t\t\tclear_inode_flag(inode, FI_ACL_MODE);
\t\t}
\t}
"""
    if old in s:
        s=rep1(s,old,new,"F2FS setattr ACL atomicity")
        out.append("F2FS_4D9_SETATTR=patched")
    elif new in s:
        out.append("F2FS_4D9_SETATTR=present")
    else:
        die("F2FS setattr ACL shape unknown")

    # 72c6b13: checkpoint only when the fsynced file's parent xattrs changed.
    old_cp="""\telse if (F2FS_OPTION(sbi).fsync_mode == FSYNC_MODE_STRICT &&
\t\tf2fs_need_dentry_mark(sbi, inode->i_ino) &&
\t\tf2fs_exist_written_data(sbi, F2FS_I(inode)->i_pino,
\t\t\t\t\t\t\tTRANS_DIR_INO))
\t\tcp_reason = CP_RECOVER_DIR;
"""
    new_cp=old_cp+"""\telse if (f2fs_exist_written_data(sbi, F2FS_I(inode)->i_pino,
\t\t\t\t\t\t\tXATTR_DIR_INO))
\t\tcp_reason = CP_XATTR_DIR;
"""
    if "cp_reason = CP_XATTR_DIR;" not in s:
        s=rep1(s,old_cp,new_cp,"F2FS XATTR checkpoint reason")
        out.append("F2FS_72C_CP_REASON=patched")
    else:
        out.append("F2FS_72C_CP_REASON=present")
    wr(root,rel,s)

    rel="fs/f2fs/f2fs.h"
    s=rd(root,rel)
    if "XATTR_DIR_INO" not in s:
        s=rep1(s,
            "\tTRANS_DIR_INO,\t\t/* for trasactions dir ino list */\n\tFLUSH_INO,",
            "\tTRANS_DIR_INO,\t\t/* for trasactions dir ino list */\n\tXATTR_DIR_INO,\t\t/* for xattr updated dir ino list */\n\tFLUSH_INO,",
            "F2FS XATTR_DIR_INO enum")
    if "CP_XATTR_DIR" not in s:
        s=rep1(s,
            "\tCP_RECOVER_DIR,\n\tNR_CP_REASON,",
            "\tCP_RECOVER_DIR,\n\tCP_XATTR_DIR,\n\tNR_CP_REASON,",
            "F2FS CP_XATTR_DIR enum")
    wr(root,rel,s)

    rel="include/trace/events/f2fs.h"
    s=rd(root,rel)
    old_trace='\t\t{ CP_RECOVER_DIR,\t"dir needs recovery" })'
    new_trace='\t\t{ CP_RECOVER_DIR,\t"dir needs recovery" },\t\t\\\n\t\t{ CP_XATTR_DIR,\t\t"dir\'s xattr updated" })'
    if "CP_XATTR_DIR" not in s:
        s=rep1(s,old_trace,new_trace,"F2FS CP_XATTR_DIR trace")
    wr(root,rel,s)

    rel="fs/f2fs/xattr.c"
    s=rd(root,rel)
    sig="static int __f2fs_setxattr(struct inode *inode, int index,"
    a,b=span(s,sig)
    x=s[a:b]

    if "struct f2fs_sb_info *sbi = F2FS_I_SB(inode);" not in x:
        x=rep1(x,
            "{\n\tstruct f2fs_xattr_entry *here, *last;",
            "{\n\tstruct f2fs_sb_info *sbi = F2FS_I_SB(inode);\n\tstruct f2fs_xattr_entry *here, *last;",
            "F2FS xattr sbi local")

    if "f2fs_xattr_value_same(here, value, size))\n\t\t\tgoto same;" not in x:
        x=rep1(x,
            "if (value && f2fs_xattr_value_same(here, value, size))\n\t\t\tgoto exit;",
            "if (value && f2fs_xattr_value_same(here, value, size))\n\t\t\tgoto same;",
            "F2FS xattr same-value ACL path")

    old_tail="""\tif (is_inode_flag_set(inode, FI_ACL_MODE)) {
\t\tinode->i_mode = F2FS_I(inode)->i_acl_mode;
\t\tinode->i_ctime = current_time(inode);
\t\tclear_inode_flag(inode, FI_ACL_MODE);
\t}
\tif (index == F2FS_XATTR_INDEX_ENCRYPTION &&
\t\t\t!strcmp(name, F2FS_XATTR_NAME_ENCRYPTION_CONTEXT))
\t\tf2fs_set_encrypted_inode(inode);
\tf2fs_mark_inode_dirty_sync(inode, true);
\tif (!error && S_ISDIR(inode->i_mode))
\t\tset_sbi_flag(F2FS_I_SB(inode), SBI_NEED_CP);
exit:
"""
    new_tail="""\tif (index == F2FS_XATTR_INDEX_ENCRYPTION &&
\t\t\t!strcmp(name, F2FS_XATTR_NAME_ENCRYPTION_CONTEXT))
\t\tf2fs_set_encrypted_inode(inode);
\tf2fs_mark_inode_dirty_sync(inode, true);

\tif (!S_ISDIR(inode->i_mode))
\t\tgoto same;
\t/*
\t * Stable 72c6b13: strict mode keeps the global checkpoint behavior;
\t * other modes track only directories whose xattrs changed.
\t */
\tif (F2FS_OPTION(sbi).fsync_mode == FSYNC_MODE_STRICT)
\t\tset_sbi_flag(sbi, SBI_NEED_CP);
\telse
\t\tf2fs_add_ino_entry(sbi, inode->i_ino, XATTR_DIR_INO);
same:
\tif (is_inode_flag_set(inode, FI_ACL_MODE)) {
\t\tinode->i_mode = F2FS_I(inode)->i_acl_mode;
\t\tinode->i_ctime = current_time(inode);
\t\tclear_inode_flag(inode, FI_ACL_MODE);
\t}

exit:
"""
    if "Stable 72c6b13" not in x:
        x=rep1(x,old_tail,new_tail,"F2FS xattr 4d9+72c combined tail")
        out.append("F2FS_4D9_72C_XATTR=patched")
    else:
        out.append("F2FS_4D9_72C_XATTR=present")

    s=s[:a]+x+s[b:]
    wr(root,rel,s)

def audit_existing(root,out):
    vm=rd(root,"mm/vmscan.c")
    if "wait_event_killable(lruvec->mm_state.wait" in vm:
        die("B3 regression: MGLRU mm_state wait restored")
    out.append("B3_MGLRU_NO_WAIT=PASS")

    lpm=rd(root,"drivers/cpuidle/lpm-levels.c")
    if "A52 QCOM LPM P1: raw idle locks + short-idle tick + hotpath cleanup" not in lpm:
        die("D3 Qualcomm LPM short-idle tick fix missing")
    if "*stop_tick = false;" not in lpm:
        die("D3 Qualcomm LPM stop_tick behavior missing")
    out.append("D3_QCOM_LPM_TICK=PASS")

def final_audit(root,out):
    inode=rd(root,"fs/fuse/inode.c")
    if "FUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT" not in inode:
        die("audit: writeback cache not offered")
    init=fun(inode,"static void process_init_reply(struct fuse_conn *fc, struct fuse_req *req)")
    m=init.find("} else if (arg->minor >= 40 &&")
    if m<0: die("audit: modern passthrough branch missing")
    modern=init[m:]
    if "!(arg->flags & FUSE_WRITEBACK_CACHE)" in modern:
        die("audit: modern passthrough still rejects writeback")
    if "fc->writeback_cache = 0;" not in modern or "fc->passthrough = 1;" not in modern:
        die("audit: modern passthrough priority incomplete")

    acl=rd(root,"fs/f2fs/acl.c")
    filec=rd(root,"fs/f2fs/file.c")
    xattr=rd(root,"fs/f2fs/xattr.c")
    hdr=rd(root,"fs/f2fs/f2fs.h")
    trace=rd(root,"include/trace/events/f2fs.h")
    checks=[
        ("f2fs_acl_update_mode",acl),
        ("if (!err)\n\t\t\t\tinode->i_mode",filec),
        ("XATTR_DIR_INO",hdr),
        ("CP_XATTR_DIR",hdr),
        ("cp_reason = CP_XATTR_DIR;",filec),
        ("Stable 72c6b13",xattr),
        ("f2fs_add_ino_entry(sbi, inode->i_ino, XATTR_DIR_INO);",xattr),
        ("CP_XATTR_DIR",trace),
    ]
    for needle,blob in checks:
        if needle not in blob:
            die(f"audit missing {needle}")
    out.append("AUDIT=PASS")

def main():
    if len(sys.argv)!=2:
        die(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    if not (root/"Makefile").is_file():
        die("not a kernel tree")
    out=[]
    patch_fuse(root,out)
    patch_f2fs_acl_checkpoint(root,out)
    audit_existing(root,out)
    final_audit(root,out)
    print("P166 FUSE negotiation + F2FS stable continuation: PASS")
    for item in out:
        print(item)
    print("baseline=P165_boot_and_fuse_tested")
    print("experiment=writeback_offer_plus_passthrough_priority")
    print("new_fixes=4d9c9b7,72c6b13")
    print("scheduler_delta=none")
    print("gpu_delta=none")

if __name__=="__main__":
    main()
# P166 workflow trigger: baseline P165 run 37003864311
