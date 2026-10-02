#!/usr/bin/env python3
from pathlib import Path
import re
import sys

P="P168"

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
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

def span(text, sig):
    a=text.find(sig)
    if a < 0:
        die(f"missing function {sig}")
    b=text.find("{",a)
    if b < 0:
        die(f"missing opening brace {sig}")
    depth=0
    for i in range(b,len(text)):
        if text[i]=="{":
            depth+=1
        elif text[i]=="}":
            depth-=1
            if depth==0:
                return a,i+1
    die(f"unterminated function {sig}")

def block(text,sig):
    a,b=span(text,sig)
    return text[a:b]

def replace_function(text,sig,new):
    a,b=span(text,sig)
    return text[:a]+new+text[b:]

def patch_fscrypt_symlink_getattr(root,out):
    rel="fs/crypto/hooks.c"
    s=rd(root,rel)
    impl="""int fscrypt_symlink_getattr(const struct path *path, struct kstat *stat)
{
\tstruct dentry *dentry = path->dentry;
\tstruct inode *inode = d_inode(dentry);
\tconst char *link;
\tDEFINE_DELAYED_CALL(done);

\tlink = READ_ONCE(inode->i_link);
\tif (!link) {
\t\tlink = inode->i_op->get_link(dentry, inode, &done);
\t\tif (IS_ERR(link))
\t\t\treturn PTR_ERR(link);
\t}
\tstat->size = strlen(link);
\tdo_delayed_call(&done);
\treturn 0;
}
EXPORT_SYMBOL_GPL(fscrypt_symlink_getattr);
"""
    if "int fscrypt_symlink_getattr(const struct path *path, struct kstat *stat)" not in s:
        anchor="EXPORT_SYMBOL_GPL(fscrypt_get_symlink);\n"
        s=rep1(s,anchor,anchor+"\n"+impl,"fscrypt helper implementation")
        wr(root,rel,s)
        out.append("FSCRYPT_SYMLINK_GETATTR_HELPER=patched")
    else:
        out.append("FSCRYPT_SYMLINK_GETATTR_HELPER=present")

    rel="include/linux/fscrypt.h"
    h=rd(root,rel)
    if "fscrypt_symlink_getattr(const struct path *path" not in h:
        proto="""const char *fscrypt_get_symlink(struct inode *inode, const void *caddr,
\t\t\t\tunsigned int max_size,
\t\t\t\tstruct delayed_call *done);
"""
        decl="int fscrypt_symlink_getattr(const struct path *path, struct kstat *stat);\n"
        h=rep1(h,proto,proto+decl,"fscrypt helper prototype")

        marker="#else  /* !CONFIG_FS_ENCRYPTION */"
        m=h.find(marker)
        if m < 0:
            die("fscrypt !CONFIG section marker missing")
        tail=h[m:]
        sig="static inline const char *fscrypt_get_symlink("
        a,b=span(tail,sig)
        stub="""static inline int fscrypt_symlink_getattr(const struct path *path,
\t\t\t\t\t  struct kstat *stat)
{
\treturn -EOPNOTSUPP;
}
"""
        tail=tail[:b]+"\n\n"+stub+tail[b:]
        h=h[:m]+tail
        wr(root,rel,h)
        out.append("FSCRYPT_SYMLINK_GETATTR_HEADER=patched")
    else:
        # Require both enabled declaration and disabled stub/definition references.
        if h.count("fscrypt_symlink_getattr(const struct path *path") < 2:
            die("fscrypt helper header only partially present")
        out.append("FSCRYPT_SYMLINK_GETATTR_HEADER=present")

def patch_f2fs_encrypted_symlink(root,out):
    rel="fs/f2fs/namei.c"
    s=rd(root,rel)
    helper="""static int f2fs_encrypted_symlink_getattr(const struct path *path,
\t\t\t\t\t  struct kstat *stat, u32 request_mask,
\t\t\t\t\t  unsigned int query_flags)
{
\tf2fs_getattr(path, stat, request_mask, query_flags);

\treturn fscrypt_symlink_getattr(path, stat);
}

"""
    ops="const struct inode_operations f2fs_encrypted_symlink_inode_operations = {"
    oi=s.find(ops)
    if oi < 0:
        die("F2FS encrypted symlink ops missing")
    if "static int f2fs_encrypted_symlink_getattr(" not in s:
        s=s[:oi]+helper+s[oi:]
        oi=s.find(ops)

    a=s.find("{",oi)
    b=s.find("};",a)
    if a < 0 or b < 0:
        die("F2FS encrypted symlink ops bounds missing")
    ob=s[oi:b+2]
    if "f2fs_encrypted_symlink_getattr" not in ob:
        ob2,n=re.subn(r"(\.getattr\s*=\s*)f2fs_getattr,",r"\1f2fs_encrypted_symlink_getattr,",ob,count=1)
        if n != 1:
            die(f"F2FS encrypted symlink getattr replacement count {n}")
        s=s[:oi]+ob2+s[b+2:]
    wr(root,rel,s)
    out.append("F2FS_8377_ENCRYPTED_SYMLINK_SIZE=patched_or_present")

def patch_f2fs_dir_gfp(root,out):
    rel="fs/f2fs/inode.c"
    s=rd(root,rel)
    sig="struct inode *f2fs_iget(struct super_block *sb, unsigned long ino)"
    a,b=span(s,sig)
    fn=s[a:b]

    ds=fn.find("} else if (S_ISDIR(inode->i_mode)) {")
    de=fn.find("} else if (S_ISLNK(inode->i_mode)) {",ds)
    if ds < 0 or de < 0:
        die("F2FS iget directory branch bounds missing")
    d=fn[ds:de]
    new_line="mapping_set_gfp_mask(inode->i_mapping, GFP_NOFS);"
    if new_line not in d:
        if d.count("inode_nohighmem(inode);") != 1:
            die(f"F2FS iget dir inode_nohighmem count {d.count('inode_nohighmem(inode);')}")
        d=d.replace("inode_nohighmem(inode);",new_line,1)
        fn=fn[:ds]+d+fn[de:]
        s=s[:a]+fn+s[b:]
    wr(root,rel,s)

    rel="fs/f2fs/namei.c"
    s=rd(root,rel)
    sig="static int f2fs_mkdir(struct inode *dir, struct dentry *dentry, umode_t mode)"
    a,b=span(s,sig)
    fn=s[a:b]
    if new_line not in fn:
        if fn.count("inode_nohighmem(inode);") != 1:
            die(f"F2FS mkdir inode_nohighmem count {fn.count('inode_nohighmem(inode);')}")
        fn=fn.replace("inode_nohighmem(inode);",new_line,1)
        s=s[:a]+fn+s[b:]
        wr(root,rel,s)
    out.append("F2FS_83DEF_DIR_GFP_NOFS=patched_or_present")

def patch_f2fs_fg_gc(root,out):
    rel="fs/f2fs/segment.h"
    s=rd(root,rel)

    cur="""static inline bool has_curseg_enough_space(struct f2fs_sb_info *sbi,
\t\t\tunsigned int node_blocks, unsigned int dent_blocks)
{
\tunsigned int segno, left_blocks;
\tint i;

\t/* check current node segment */
\tfor (i = CURSEG_HOT_NODE; i <= CURSEG_COLD_NODE; i++) {
\t\tsegno = CURSEG_I(sbi, i)->segno;
\t\tleft_blocks = sbi->blocks_per_seg -
\t\t\tget_seg_entry(sbi, segno)->ckpt_valid_blocks;

\t\tif (node_blocks > left_blocks)
\t\t\treturn false;
\t}

\t/* check current data segment */
\tsegno = CURSEG_I(sbi, CURSEG_HOT_DATA)->segno;
\tleft_blocks = sbi->blocks_per_seg -
\t\t\tget_seg_entry(sbi, segno)->ckpt_valid_blocks;
\tif (dent_blocks > left_blocks)
\t\treturn false;
\treturn true;
}"""
    free="""static inline bool has_not_enough_free_secs(struct f2fs_sb_info *sbi,
\t\t\t\t\tint freed, int needed)
{
\tunsigned int total_node_blocks = get_pages(sbi, F2FS_DIRTY_NODES) +
\t\t\t\t\tget_pages(sbi, F2FS_DIRTY_DENTS) +
\t\t\t\t\tget_pages(sbi, F2FS_DIRTY_IMETA);
\tunsigned int total_dent_blocks = get_pages(sbi, F2FS_DIRTY_DENTS);
\tunsigned int node_secs = total_node_blocks / BLKS_PER_SEC(sbi);
\tunsigned int dent_secs = total_dent_blocks / BLKS_PER_SEC(sbi);
\tunsigned int node_blocks = total_node_blocks % BLKS_PER_SEC(sbi);
\tunsigned int dent_blocks = total_dent_blocks % BLKS_PER_SEC(sbi);
\tunsigned int free, need_lower, need_upper;

\tif (unlikely(is_sbi_flag_set(sbi, SBI_POR_DOING)))
\t\treturn false;

\tfree = free_sections(sbi) + freed;
\tneed_lower = node_secs + dent_secs + reserved_sections(sbi) + needed;
\tneed_upper = need_lower + (node_blocks ? 1 : 0) + (dent_blocks ? 1 : 0);

\tif (free > need_upper)
\t\treturn false;
\telse if (free <= need_lower)
\t\treturn true;
\treturn !has_curseg_enough_space(sbi, node_blocks, dent_blocks);
}"""

    sig="static inline bool has_curseg_enough_space(struct f2fs_sb_info *sbi"
    old=block(s,sig)
    if "unsigned int node_blocks, unsigned int dent_blocks" not in old:
        s=replace_function(s,sig,cur)

    sig2="static inline bool has_not_enough_free_secs(struct f2fs_sb_info *sbi,"
    old2=block(s,sig2)
    if "need_upper = need_lower" not in old2 or "F2FS_DIRTY_IMETA" not in old2:
        s=replace_function(s,sig2,free)

    wr(root,rel,s)
    out.append("F2FS_6CC208_FG_GC_DEADLOOP=patched_or_present")

def patch_f2fs_wbc_owner(root,out):
    rel="fs/f2fs/data.c"
    s=rd(root,rel)

    sig="int f2fs_submit_page_bio(struct f2fs_io_info *fio)"
    a,b=span(s,sig)
    fn=s[a:b]
    old="wbc_account_io(fio->io_wbc, page, PAGE_SIZE);"
    new="wbc_account_io(fio->io_wbc, fio->page, PAGE_SIZE);"
    if new not in fn:
        if fn.count(old) != 1:
            die(f"F2FS submit_page_bio wbc anchor count {fn.count(old)}")
        fn=fn.replace(old,new,1)
        s=s[:a]+fn+s[b:]

    sig="void f2fs_submit_page_write(struct f2fs_io_info *fio)"
    a,b=span(s,sig)
    fn=s[a:b]
    old="wbc_account_io(fio->io_wbc, bio_page, PAGE_SIZE);"
    new="wbc_account_io(fio->io_wbc, fio->page, PAGE_SIZE);"
    if new not in fn:
        if fn.count(old) != 1:
            die(f"F2FS submit_page_write wbc anchor count {fn.count(old)}")
        fn=fn.replace(old,new,1)
        s=s[:a]+fn+s[b:]

    wr(root,rel,s)
    out.append("F2FS_BCCAE_WBC_OWNER=patched_or_present")

def patch_zram_block_state(root,out):
    rel="drivers/block/zram/zram_drv.c"
    s=rd(root,rel)
    sig="static ssize_t read_block_state(struct file *file, char __user *buf,"
    a,b=span(s,sig)
    fn=s[a:b]
    if "if (count <= copied)" not in fn:
        if fn.count("if (count < copied)") != 1:
            die(f"ZRAM read_block_state comparison count {fn.count('if (count < copied)')}")
        fn=fn.replace("if (count < copied)","if (count <= copied)",1)
        s=s[:a]+fn+s[b:]
        wr(root,rel,s)
    out.append("ZRAM_26DF52_BLOCK_STATE_OBO=patched_or_present")

def patch_zsmalloc_compaction_accounting(root,out):
    rel="include/linux/zsmalloc.h"
    h=rd(root,rel)
    if "atomic_long_t pages_compacted;" not in h:
        h=rep1(h,
            "\tunsigned long pages_compacted;\n",
            "\tatomic_long_t pages_compacted;\n",
            "zsmalloc pages_compacted type")
        wr(root,rel,h)

    rel="mm/zsmalloc.c"
    s=rd(root,rel)
    sig="static void __zs_compact(struct zs_pool *pool, struct size_class *class)"
    if sig in s:
        old=block(s,sig)
        new=old.replace("static void __zs_compact(", "static unsigned long __zs_compact(",1)
        new=new.replace("\tstruct zspage *dst_zspage = NULL;\n",
                        "\tstruct zspage *dst_zspage = NULL;\n\tunsigned long pages_freed = 0;\n",1)
        if "pool->stats.pages_compacted += class->pages_per_zspage;" not in new:
            die("zsmalloc old pages_compacted increment missing")
        new=new.replace("pool->stats.pages_compacted += class->pages_per_zspage;",
                        "pages_freed += class->pages_per_zspage;",1)
        end=new.rfind("\n}")
        if end < 0:
            die("zsmalloc __zs_compact end missing")
        new=new[:end]+"\n\treturn pages_freed;"+new[end:]
        s=replace_function(s,sig,new)

    sig="unsigned long zs_compact(struct zs_pool *pool)"
    a,b=span(s,sig)
    fn=s[a:b]
    if "atomic_long_add(pages_freed, &pool->stats.pages_compacted);" not in fn:
        fn=fn.replace("\tstruct size_class *class;\n",
                      "\tstruct size_class *class;\n\tunsigned long pages_freed = 0;\n",1)
        if "__zs_compact(pool, class);" not in fn:
            die("zsmalloc zs_compact call anchor missing")
        fn=fn.replace("__zs_compact(pool, class);","pages_freed += __zs_compact(pool, class);")
        if "return pool->stats.pages_compacted;" not in fn:
            die("zsmalloc old zs_compact return missing")
        fn=fn.replace("return pool->stats.pages_compacted;",
                      "atomic_long_add(pages_freed, &pool->stats.pages_compacted);\n\n\treturn pages_freed;",1)
        s=s[:a]+fn+s[b:]

    sig="static unsigned long zs_shrinker_scan(struct shrinker *shrinker,"
    a,b=span(s,sig)
    fn=s[a:b]
    if "pages_freed = zs_compact(pool);" not in fn:
        fn=re.sub(r"\n\tpages_freed = pool->stats\.pages_compacted;\n","\n",fn,count=1)
        old="pages_freed = zs_compact(pool) - pages_freed;"
        if old not in fn:
            die("zsmalloc shrinker delta anchor missing")
        fn=fn.replace(old,"pages_freed = zs_compact(pool);",1)
        s=s[:a]+fn+s[b:]

    wr(root,rel,s)

    rel="drivers/block/zram/zram_drv.c"
    z=rd(root,rel)
    sig="static ssize_t mm_stat_show(struct device *dev,"
    a,b=span(z,sig)
    fn=z[a:b]
    if "atomic_long_read(&pool_stats.pages_compacted)" not in fn:
        if fn.count("pool_stats.pages_compacted") != 1:
            die(f"zram mm_stat pages_compacted count {fn.count('pool_stats.pages_compacted')}")
        fn=fn.replace("pool_stats.pages_compacted",
                      "atomic_long_read(&pool_stats.pages_compacted)",1)
        z=z[:a]+fn+z[b:]
        wr(root,rel,z)

    out.append("ZSMALLOC_638C954_COMPACTION_ACCOUNTING=patched_or_present")

def audit_closed_p118_skips(root,out):
    # 3506e1 is already present semantically in Samsung's newer F2FS.
    seg=rd(root,"fs/f2fs/segment.c")
    sup=rd(root,"fs/f2fs/super.c")
    if "invalid journal entries nats %u sits %u" not in seg:
        die("P118 skip 3506e1 diagnostic journal check missing")
    if "Failed to initialize F2FS segment manager (%d)" not in sup:
        die("P118 skip 3506e1 segment-manager errno log missing")
    if "Failed to initialize F2FS node manager (%d)" not in sup:
        die("P118 skip 3506e1 node-manager errno log missing")
    out.append("P118_3506E1=already_semantic")

    # c1ea7a86 is subsumed by P166/P167's xattr control-flow rewrite.
    x=rd(root,"fs/f2fs/xattr.c")
    a,b=span(x,"static int __f2fs_setxattr(struct inode *inode, int index,")
    xf=x[a:b]
    if "if (!error && S_ISDIR(inode->i_mode))" in xf:
        die("P118 skip c1ea7a86 obsolete !error directory check remains")
    if "if (!S_ISDIR(inode->i_mode))" not in xf:
        die("P118 skip c1ea7a86 P166 directory flow missing")
    out.append("P118_C1EA7A86=already_semantic")

def final_audit(root,out):
    hooks=rd(root,"fs/crypto/hooks.c")
    hdr=rd(root,"include/linux/fscrypt.h")
    namei=rd(root,"fs/f2fs/namei.c")
    inode=rd(root,"fs/f2fs/inode.c")
    segh=rd(root,"fs/f2fs/segment.h")
    data=rd(root,"fs/f2fs/data.c")
    zram=rd(root,"drivers/block/zram/zram_drv.c")
    zsh=rd(root,"include/linux/zsmalloc.h")
    zs=rd(root,"mm/zsmalloc.c")

    checks=[
        ("fscrypt helper impl","EXPORT_SYMBOL_GPL(fscrypt_symlink_getattr);" in hooks),
        ("fscrypt helper declarations",hdr.count("fscrypt_symlink_getattr(const struct path *path") >= 2),
        ("f2fs encrypted getattr","static int f2fs_encrypted_symlink_getattr(" in namei),
        ("f2fs encrypted ops",".getattr\t= f2fs_encrypted_symlink_getattr," in namei or
                              ".getattr = f2fs_encrypted_symlink_getattr," in namei),
        ("f2fs dir GFP_NOFS",block(inode,"struct inode *f2fs_iget(").count(
            "mapping_set_gfp_mask(inode->i_mapping, GFP_NOFS);") >= 3),
        ("f2fs mkdir GFP_NOFS","mapping_set_gfp_mask(inode->i_mapping, GFP_NOFS);" in
            block(namei,"static int f2fs_mkdir(")),
        ("f2fs fg gc lower","need_lower = node_secs + dent_secs + reserved_sections(sbi) + needed;" in segh),
        ("f2fs fg gc curseg","has_curseg_enough_space(sbi, node_blocks, dent_blocks)" in segh),
        ("f2fs wbc submit bio","wbc_account_io(fio->io_wbc, fio->page, PAGE_SIZE);" in
            block(data,"int f2fs_submit_page_bio(")),
        ("f2fs wbc submit write","wbc_account_io(fio->io_wbc, fio->page, PAGE_SIZE);" in
            block(data,"void f2fs_submit_page_write(")),
        ("zram block state","if (count <= copied)" in block(zram,"static ssize_t read_block_state(")),
        ("zsmalloc atomic type","atomic_long_t pages_compacted;" in zsh),
        ("zsmalloc local compact","static unsigned long __zs_compact(" in zs),
        ("zsmalloc total accounting","atomic_long_add(pages_freed, &pool->stats.pages_compacted);" in zs),
        ("zsmalloc shrinker return","pages_freed = zs_compact(pool);" in block(zs,"static unsigned long zs_shrinker_scan(")),
        ("zram atomic stat","atomic_long_read(&pool_stats.pages_compacted)" in block(zram,"static ssize_t mm_stat_show(")),
    ]
    bad=[n for n,ok in checks if not ok]
    if bad:
        die("audit failures: "+", ".join(bad))

    # Preserve P167 and the explicitly rejected legacy MGLRU behaviors.
    vm=rd(root,"mm/vmscan.c")
    if "wait_event_killable(lruvec->mm_state.wait" in vm:
        die("B3 regression: rejected MGLRU wait restored")
    if "size += max_t(long," not in block(vm,"static long get_nr_evictable("):
        die("P167 MGLRU evictable read hardening lost")

    fuse=rd(root,"fs/fuse/inode.c")
    init=block(fuse,"static void process_init_reply(struct fuse_conn *fc, struct fuse_req *req)")
    m=init.find("} else if (arg->minor >= 40 &&")
    if m < 0 or "fc->writeback_cache = 0;" not in init[m:] or "fc->passthrough = 1;" not in init[m:]:
        die("P166 FUSE negotiation lost")

    out.append("AUDIT=PASS")

def main():
    if len(sys.argv)!=2:
        die(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    if not (root/"Makefile").is_file():
        die("not a kernel tree")

    out=[]
    patch_fscrypt_symlink_getattr(root,out)
    patch_f2fs_encrypted_symlink(root,out)
    patch_f2fs_dir_gfp(root,out)
    patch_f2fs_fg_gc(root,out)
    patch_f2fs_wbc_owner(root,out)
    patch_zram_block_state(root,out)
    patch_zsmalloc_compaction_accounting(root,out)
    audit_closed_p118_skips(root,out)
    final_audit(root,out)

    print("P168 storage/memory correctness completion: PASS")
    for x in out:
        print(x)
    print("baseline=P167_boot_and_fuse_tested")
    print("code_changes=7")
    print("f2fs_fixes=8377a6af,83def434,6cc20869,bccae81d")
    print("fscrypt_dependency=symlink_getattr")
    print("zram_fix=26df52b3")
    print("zsmalloc_fix=638c9546")
    print("p118_skips_semantically_already_closed=3506e1b8,c1ea7a86")
    print("scheduler_delta=none")
    print("gpu_delta=none")
    print("fuse_delta=none")

if __name__=="__main__":
    main()
