#!/usr/bin/env python3
from pathlib import Path
import sys

P="P167"

def die(msg):
    raise SystemExit(f"{P}: {msg}")

def rd(root, rel):
    p=root/rel
    if not p.is_file():
        die(f"missing {rel}")
    return p.read_text()

def wr(root, rel, text):
    (root/rel).write_text(text)

def span(text, sig):
    a=text.find(sig)
    if a < 0:
        die(f"missing function {sig}")
    b=text.find("{", a)
    if b < 0:
        die(f"missing opening brace {sig}")
    depth=0
    for i in range(b, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return a, i + 1
    die(f"unterminated function {sig}")

def rep1(text, old, new, label):
    n=text.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

def patch_mglru_b6(root, out):
    rel="mm/vmscan.c"
    s=rd(root, rel)
    sig="static long get_nr_evictable(struct lruvec *lruvec, unsigned long max_seq,"
    a,b=span(s, sig)
    fn=s[a:b]

    old="""\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++)
\t\t\t\tsize += READ_ONCE(lrugen->nr_pages[gen][type][zone]);
"""
    new="""\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++)
\t\t\t\tsize += max_t(long,
\t\t\t\t\tREAD_ONCE(lrugen->nr_pages[gen][type][zone]), 0);
"""
    if new in fn:
        out.append("B6_MGLRU_NEGATIVE_SIZE_CLAMP=present")
    elif old in fn:
        fn=rep1(fn, old, new, "B6 get_nr_evictable per-zone clamp")
        s=s[:a]+fn+s[b:]
        wr(root, rel, s)
        out.append("B6_MGLRU_NEGATIVE_SIZE_CLAMP=patched")
    else:
        die("B6 get_nr_evictable size accumulation shape unknown")

    fn=rd(root, rel)
    a,b=span(fn, sig)
    fn=fn[a:b]
    if "size += max_t(long," not in fn or "READ_ONCE(lrugen->nr_pages[gen][type][zone]), 0);" not in fn:
        die("B6 clamp missing after patch")

def patch_f2fs_readonly(root, out):
    rel="fs/f2fs/inode.c"
    s=rd(root, rel)
    sig="void f2fs_mark_inode_dirty_sync(struct inode *inode, bool sync)"
    a,b=span(s, sig)
    fn=s[a:b]

    guard="""\tif (f2fs_readonly(F2FS_I_SB(inode)->sb))
\t\treturn;
"""
    if guard in fn:
        out.append("F2FS_2D291_READONLY_DIRTY=present")
        return

    anchor="""\tif (is_inode_flag_set(inode, FI_NEW_INODE))
\t\treturn;

"""
    if anchor not in fn:
        die("F2FS 2d291 FI_NEW_INODE anchor missing")
    fn=rep1(fn, anchor, anchor+guard+"\n", "F2FS 2d291 readonly guard")
    s=s[:a]+fn+s[b:]
    wr(root, rel, s)
    out.append("F2FS_2D291_READONLY_DIRTY=patched")

def patch_f2fs_area_overflow(root, out):
    rel="fs/f2fs/super.c"
    s=rd(root, rel)
    sig="static inline bool sanity_check_area_boundary(struct f2fs_sb_info *sbi,"
    a,b=span(s, sig)
    fn=s[a:b]

    old1="(segment_count_main << log_blocks_per_seg)"
    new1="((u64)segment_count_main << log_blocks_per_seg)"
    old2="(segment_count << log_blocks_per_seg)"
    new2="((u64)segment_count << log_blocks_per_seg)"

    changed=False
    if new1 not in fn:
        if fn.count(old1) != 1:
            die(f"F2FS 24dfe main area anchor count {fn.count(old1)}")
        fn=fn.replace(old1,new1,1)
        changed=True
    if new2 not in fn:
        if fn.count(old2) != 1:
            die(f"F2FS 24dfe segment area anchor count {fn.count(old2)}")
        fn=fn.replace(old2,new2,1)
        changed=True

    if changed:
        s=s[:a]+fn+s[b:]
        wr(root, rel, s)
        out.append("F2FS_24DFE_AREA_U64=patched")
    else:
        out.append("F2FS_24DFE_AREA_U64=present")

def patch_f2fs_xattr_ctime(root, out):
    rel="fs/f2fs/xattr.c"
    s=rd(root, rel)
    sig="static int __f2fs_setxattr(struct inode *inode, int index,"
    a,b=span(s, sig)
    fn=s[a:b]

    # P166 intentionally moved directory checkpoint selection ahead of 'same:'.
    # Stable eb926232 requires ctime and dirtying at 'same:' so both normal
    # writes and same-value xattr operations update inode metadata correctly.
    desired="""same:
\tif (is_inode_flag_set(inode, FI_ACL_MODE)) {
\t\tinode->i_mode = F2FS_I(inode)->i_acl_mode;
\t\tclear_inode_flag(inode, FI_ACL_MODE);
\t}

\tinode->i_ctime = current_time(inode);
\tf2fs_mark_inode_dirty_sync(inode, true);

exit:
"""
    if desired in fn:
        out.append("F2FS_EB926_XATTR_CTIME=present")
        return

    old="""\tf2fs_mark_inode_dirty_sync(inode, true);

\tif (!S_ISDIR(inode->i_mode))
\t\tgoto same;
"""
    new="""\tif (!S_ISDIR(inode->i_mode))
\t\tgoto same;
"""
    if old not in fn:
        die("F2FS eb926 pre-checkpoint dirty anchor missing")
    fn=rep1(fn,old,new,"F2FS eb926 move dirtying to same label")

    old_same="""same:
\tif (is_inode_flag_set(inode, FI_ACL_MODE)) {
\t\tinode->i_mode = F2FS_I(inode)->i_acl_mode;
\t\tinode->i_ctime = current_time(inode);
\t\tclear_inode_flag(inode, FI_ACL_MODE);
\t}

exit:
"""
    if old_same not in fn:
        die("F2FS eb926 P166 same-label shape missing")
    fn=rep1(fn,old_same,desired,"F2FS eb926 ctime/dirty ordering")

    s=s[:a]+fn+s[b:]
    wr(root,rel,s)
    out.append("F2FS_EB926_XATTR_CTIME=patched")

def audit(root,out):
    v=rd(root,"mm/vmscan.c")
    a,b=span(v,"static long get_nr_evictable(struct lruvec *lruvec, unsigned long max_seq,")
    ev=v[a:b]
    if "size += max_t(long," not in ev:
        die("audit: B6 MGLRU clamp missing")

    inode=rd(root,"fs/f2fs/inode.c")
    a,b=span(inode,"void f2fs_mark_inode_dirty_sync(struct inode *inode, bool sync)")
    dirty=inode[a:b]
    if "f2fs_readonly(F2FS_I_SB(inode)->sb)" not in dirty:
        die("audit: F2FS readonly guard missing")

    sup=rd(root,"fs/f2fs/super.c")
    a,b=span(sup,"static inline bool sanity_check_area_boundary(struct f2fs_sb_info *sbi,")
    san=sup[a:b]
    if "((u64)segment_count_main << log_blocks_per_seg)" not in san:
        die("audit: main area u64 cast missing")
    if "((u64)segment_count << log_blocks_per_seg)" not in san:
        die("audit: segment area u64 cast missing")

    x=rd(root,"fs/f2fs/xattr.c")
    a,b=span(x,"static int __f2fs_setxattr(struct inode *inode, int index,")
    xf=x[a:b]
    same=xf.find("same:")
    exitp=xf.find("exit:",same)
    if same < 0 or exitp < 0:
        die("audit: xattr same/exit labels missing")
    tail=xf[same:exitp]
    if tail.count("inode->i_ctime = current_time(inode);") != 1:
        die("audit: xattr ctime not exactly once at same path")
    if tail.count("f2fs_mark_inode_dirty_sync(inode, true);") != 1:
        die("audit: xattr dirty sync not exactly once at same path")
    prefix=xf[:same]
    if "f2fs_mark_inode_dirty_sync(inode, true);" in prefix:
        die("audit: stale xattr dirty sync remains before same label")

    # Retain the P166 FUSE negotiation experiment unchanged.
    fuse=rd(root,"fs/fuse/inode.c")
    if "FUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT" not in fuse:
        die("audit: P166 writeback offer lost")
    a,b=span(fuse,"static void process_init_reply(struct fuse_conn *fc, struct fuse_req *req)")
    init=fuse[a:b]
    m=init.find("} else if (arg->minor >= 40 &&")
    if m < 0:
        die("audit: P166 modern passthrough branch missing")
    modern=init[m:]
    if "fc->writeback_cache = 0;" not in modern or "fc->passthrough = 1;" not in modern:
        die("audit: P166 passthrough priority lost")
    if "!(arg->flags & FUSE_WRITEBACK_CACHE)" in modern:
        die("audit: old P165 passthrough exclusion returned")

    out.append("AUDIT=PASS")

def main():
    if len(sys.argv)!=2:
        die(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    if not (root/"Makefile").is_file():
        die("not a kernel tree")

    out=[]
    patch_mglru_b6(root,out)
    patch_f2fs_readonly(root,out)
    patch_f2fs_area_overflow(root,out)
    patch_f2fs_xattr_ctime(root,out)
    audit(root,out)

    print("P167 MGLRU/F2FS correctness continuation: PASS")
    for x in out:
        print(x)
    print("baseline=P166_boot_and_fuse_tested")
    print("new_fixes=B6,2d291651,24dfe070,eb926232")
    print("fuse_delta=none")
    print("scheduler_delta=none")
    print("gpu_delta=none")

if __name__=="__main__":
    main()
