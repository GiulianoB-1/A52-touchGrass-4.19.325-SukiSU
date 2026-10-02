#!/usr/bin/env python3
from pathlib import Path
import re, sys

P="P165"
def die(x): raise SystemExit(f"{P}: {x}")
def rd(r,p):
    f=r/p
    if not f.is_file(): die(f"missing {p}")
    return f.read_text()
def wr(r,p,s): (r/p).write_text(s)
def span(s,sig):
    a=s.find(sig)
    if a<0: die(f"missing function {sig}")
    b=s.find("{",a)
    d=0
    for i in range(b,len(s)):
        d += (s[i]=="{")-(s[i]=="}")
        if d==0: return a,i+1
    die(f"unterminated {sig}")
def fun(s,sig):
    a,b=span(s,sig); return s[a:b]

def fuse(r,o):
    p="fs/fuse/inode.c"; s=rd(r,p)
    bad="FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |\n\t\tFUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT |"
    good="FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |\n\t\tFUSE_NO_OPEN_SUPPORT |"
    if bad in s: s=s.replace(bad,good,1); o.append("D6_FUSE=rolled_back")
    elif good in s: o.append("D6_FUSE=already_good")
    else: die("unknown FUSE capability list")
    if "!(arg->flags & FUSE_WRITEBACK_CACHE)" not in s: die("FUSE PT/writeback exclusion missing")
    if "FUSE_PASSTHROUGH_UPSTREAM" not in rd(r,"include/uapi/linux/fuse.h"): die("FUSE 7.40 PT UAPI missing")
    wr(r,p,s)

def bpf(r,o):
    p="kernel/bpf/ringbuf.c"; s=rd(r,p)
    sig="static int ringbuf_map_mmap(struct bpf_map *map, struct vm_area_struct *vma)"
    a,b=span(s,sig); x=s[a:b]
    if "allow writable mapping for the consumer_pos only" not in x:
        x="""static int ringbuf_map_mmap(struct bpf_map *map, struct vm_area_struct *vma)
{
	struct bpf_ringbuf_map *rb_map;

	rb_map = container_of(map, struct bpf_ringbuf_map, map);
	if (vma->vm_flags & VM_WRITE) {
		/* allow writable mapping for the consumer_pos only */
		if (vma->vm_pgoff != 0 ||
		    vma->vm_end - vma->vm_start != PAGE_SIZE)
			return -EPERM;
	} else {
		vma->vm_flags &= ~VM_MAYWRITE;
	}
	return remap_vmalloc_range(vma, rb_map->rb,
				   vma->vm_pgoff + RINGBUF_PGOFF);
}"""
        s=s[:a]+x+s[b:]; o.append("A2_BPF_MMAP=patched")
    else: o.append("A2_BPF_MMAP=present")
    wr(r,p,s)

    p="kernel/bpf/verifier.c"; s=rd(r,p)
    sig="static int check_func_arg(struct bpf_verifier_env *env, u32 regno,"
    a,b=span(s,sig); x=s[a:b]
    mark="ringbuf alloc_mem helper requires an unmodified reservation pointer"
    if mark not in x:
        q=x.find("} else if (arg_type_is_alloc_mem_ptr(arg_type)) {")
        e=x.find("} else if (arg_type_is_int_ptr(arg_type)) {",q)
        if q<0 or e<0: die("BPF alloc_mem branch missing")
        z=x[q:e]
        # Vendor 4.19 emits this block with spaces rather than the donor tab layout.
        # Match the semantic type check and splice after it. This deliberately
        # avoids re.sub replacement escaping.
        pat=re.compile(
            r"else if \(type != expected_type\)\s*\n\s*goto err_type;"
        )
        vendor_shape="""} else if (arg_type_is_alloc_mem_ptr(arg_type)) {
            expected_type = PTR_TO_MEM;
            if (register_is_null(reg) &&
                arg_type == ARG_PTR_TO_ALLOC_MEM_OR_NULL)
                /* final test in check_stack_boundary() */;
            else if (type != expected_type)
                goto err_type;
            if (meta->ptr_id || !reg->id) {
"""
        if len(pat.findall(vendor_shape)) != 1:
            die("internal BPF vendor-shape regex self-test failed")
        m=pat.search(z)
        if not m:
            die("BPF alloc_mem semantic type-check not found")
        insert="""

        /*
         * Upstream 64620e0a: submit/discard use ARG_PTR_TO_ALLOC_MEM and
         * must receive the exact reservation pointer returned by reserve().
         */
        if (arg_type == ARG_PTR_TO_ALLOC_MEM &&
            (reg->off || !tnum_is_const(reg->var_off) ||
             reg->var_off.value)) {
                verbose(env, "R%d ringbuf alloc_mem helper requires an unmodified reservation pointer\\n",
                        regno);
                return -EACCES;
        }"""
        z=z[:m.end()]+insert+z[m.end():]
        x=x[:q]+z+x[e:]; s=s[:a]+x+s[b:]
        o.append("A2_BPF_OFFSET=patched")
    else: o.append("A2_BPF_OFFSET=present")
    wr(r,p,s)

def f2fs(r,o):
    p="fs/f2fs/gc.c"; s=rd(r,p)

    # Stable GC retry accounting: reset the per-attempt rwsem counter at gc_more.
    old="\tcpc.reason = __get_cp_reason(sbi);\n\tsbi->skipped_gc_rwsem = 0;\n\tfirst_skipped = last_skipped;\ngc_more:\n"
    new="\tcpc.reason = __get_cp_reason(sbi);\n\tfirst_skipped = last_skipped;\ngc_more:\n\tsbi->skipped_gc_rwsem = 0;\n"
    if old in s:
        s=s.replace(old,new,1); o.append("A3_F2FS_GC_RETRY=patched")
    elif new in s:
        o.append("A3_F2FS_GC_RETRY=present")
    else:
        die("F2FS gc_more counter anchor unknown")

    # c782e68 + 89659bf: reject out-of-range inode NIDs and release node_page.
    alive_sig="static bool is_alive(struct f2fs_sb_info *sbi,"
    aa,ab=span(s,alive_sig); alive=s[aa:ab]
    safe_nid="""\tif (f2fs_check_nid_range(sbi, dni->ino)) {
\t\tf2fs_put_page(node_page, 1);
\t\treturn false;
\t}
"""
    if safe_nid not in alive:
        unsafe_nid="\tif (f2fs_check_nid_range(sbi, dni->ino))\n\t\treturn false;\n"
        if unsafe_nid in alive:
            alive=alive.replace(unsafe_nid,safe_nid,1)
        elif "f2fs_check_nid_range(sbi, dni->ino)" not in alive:
            anchor="\t*nofs = ofs_of_node(node_page);\n"
            if alive.count(anchor)!=1:
                die(f"F2FS is_alive insertion anchor count {alive.count(anchor)}")
            alive=alive.replace(anchor,safe_nid+"\n"+anchor,1)
        else:
            die("F2FS is_alive NID-check shape unknown")
        s=s[:aa]+alive+s[ab:]
        o.append("A3_F2FS_IS_ALIVE_NID=patched")
    else:
        o.append("A3_F2FS_IS_ALIVE_NID=present")

    # 45c9da0: never migrate data through a special inode during GC.
    gc_sig="static int gc_data_segment(struct f2fs_sb_info *sbi,"
    ga,gb=span(s,gc_sig); gc=s[ga:gb]
    if "special_file(inode->i_mode)" not in gc:
        old_cond="\t\t\tif (IS_ERR(inode) || is_bad_inode(inode)) {\n"
        new_cond="\t\t\tif (IS_ERR(inode) || is_bad_inode(inode) ||\n\t\t\t\t\tspecial_file(inode->i_mode)) {\n"
        if gc.count(old_cond)!=1:
            die(f"F2FS special-inode condition count {gc.count(old_cond)}")
        gc=gc.replace(old_cond,new_cond,1)
        o.append("A3_F2FS_SPECIAL_INODE=patched")
    else:
        o.append("A3_F2FS_SPECIAL_INODE=present")

    # 8002259: READ rwsem trylock failures must contribute to skipped_gc_rwsem.
    read_bad="\t\t\t\tif (!down_write_trylock(&fi->i_gc_rwsem[READ]))\n\t\t\t\t\tcontinue;\n"
    read_good="""\t\t\t\tif (!down_write_trylock(&fi->i_gc_rwsem[READ])) {
\t\t\t\t\tsbi->skipped_gc_rwsem++;
\t\t\t\t\tcontinue;
\t\t\t\t}
"""
    if read_good not in gc:
        if gc.count(read_bad)!=1:
            die(f"F2FS READ rwsem accounting anchor count {gc.count(read_bad)}")
        gc=gc.replace(read_bad,read_good,1)
        o.append("A3_F2FS_RWSEM_ACCOUNTING=patched")
    else:
        o.append("A3_F2FS_RWSEM_ACCOUNTING=present")
    s=s[:ga]+gc+s[gb:]
    wr(r,p,s)

    # 92575f0 part 1/2: when unmounting after cp_error, drop dirty meta pages
    # and do not leave writeback waiting forever.
    p="fs/f2fs/checkpoint.c"; s=rd(r,p)
    sig="static int __f2fs_write_meta_page(struct page *page,"
    ca,cb=span(s,sig); cp=s[ca:cb]
    close_meta="""\tif (unlikely(f2fs_cp_error(sbi))) {
\t\tif (is_sbi_flag_set(sbi, SBI_IS_CLOSE)) {
\t\t\tClearPageUptodate(page);
\t\t\tdec_page_count(sbi, F2FS_DIRTY_META);
\t\t\tunlock_page(page);
\t\t\treturn 0;
\t\t}
\t\tgoto redirty_out;
\t}
"""
    if close_meta not in cp:
        old_meta="\tif (unlikely(f2fs_cp_error(sbi)))\n\t\tgoto redirty_out;\n"
        if cp.count(old_meta)!=1:
            die(f"F2FS cp_error meta anchor count {cp.count(old_meta)}")
        cp=cp.replace(old_meta,close_meta,1)
        s=s[:ca]+cp+s[cb:]
        o.append("A3_F2FS_CPERROR_META=patched")
    else:
        o.append("A3_F2FS_CPERROR_META=present")

    wait_sig="void f2fs_wait_on_all_pages_writeback(struct f2fs_sb_info *sbi)"
    wa,wb=span(s,wait_sig); wait=s[wa:wb]
    old_wait="\t\tif (unlikely(f2fs_cp_error(sbi)))\n\t\t\tbreak;\n"
    new_wait="\t\tif (unlikely(f2fs_cp_error(sbi) &&\n\t\t\t!is_sbi_flag_set(sbi, SBI_IS_CLOSE)))\n\t\t\tbreak;\n"
    if new_wait not in wait:
        if wait.count(old_wait)!=1:
            die(f"F2FS cp_error wait anchor count {wait.count(old_wait)}")
        wait=wait.replace(old_wait,new_wait,1)
        s=s[:wa]+wait+s[wb:]
        o.append("A3_F2FS_CPERROR_WAIT=patched")
    else:
        o.append("A3_F2FS_CPERROR_WAIT=present")
    wr(r,p,s)

    # 92575f0 part 3: during close, dirty directory data pages may be dropped too.
    p="fs/f2fs/data.c"; s=rd(r,p)
    sig="static int __write_data_page(struct page *page, bool *submitted,"
    da,db=span(s,sig); data=s[da:db]
    old_dir="\t\tif (S_ISDIR(inode->i_mode))\n\t\t\tgoto redirty_out;\n"
    new_dir="\t\tif (S_ISDIR(inode->i_mode) &&\n\t\t\t\t!is_sbi_flag_set(sbi, SBI_IS_CLOSE))\n\t\t\tgoto redirty_out;\n"
    if new_dir not in data:
        if data.count(old_dir)!=1:
            die(f"F2FS cp_error data-page anchor count {data.count(old_dir)}")
        data=data.replace(old_dir,new_dir,1)
        s=s[:da]+data+s[db:]
        o.append("A3_F2FS_CPERROR_DATA=patched")
    else:
        o.append("A3_F2FS_CPERROR_DATA=present")
    wr(r,p,s)

    # Final F2FS semantic audit.
    gc=fun(rd(r,"fs/f2fs/gc.c"),gc_sig)
    alive=fun(rd(r,"fs/f2fs/gc.c"),alive_sig)
    if safe_nid not in alive:
        die("F2FS is_alive NID/page-release fix missing after patch")
    if "special_file(inode->i_mode)" not in gc:
        die("F2FS special-inode GC fix missing after patch")
    if read_good not in gc:
        die("F2FS skipped_gc_rwsem READ accounting missing after patch")
    cp=fun(rd(r,"fs/f2fs/checkpoint.c"),sig.replace("__write_data_page","__f2fs_write_meta_page") if False else "static int __f2fs_write_meta_page(struct page *page,")
    if not all(v in cp for v in ("SBI_IS_CLOSE","ClearPageUptodate(page);","dec_page_count(sbi, F2FS_DIRTY_META);")):
        die("F2FS cp_error close meta handling missing after patch")
    wait=fun(rd(r,"fs/f2fs/checkpoint.c"),wait_sig)
    if "!is_sbi_flag_set(sbi, SBI_IS_CLOSE)" not in wait:
        die("F2FS cp_error close writeback-wait handling missing")
    data=fun(rd(r,"fs/f2fs/data.c"),"static int __write_data_page(struct page *page, bool *submitted,")
    if "S_ISDIR(inode->i_mode) &&" not in data or "SBI_IS_CLOSE" not in data:
        die("F2FS cp_error close data-page handling missing")

def mglru(r,o):
    p="mm/vmscan.c"; s=rd(r,p)
    bad="\tif (!sc.nr_reclaimed) {\n\t\tcount_vm_event(LRU_KSWAPD_NO_PROGRESS);\n\t\tif (!lru_gen_enabled())\n\t\t\tpgdat->kswapd_failures++;\n\t}\n"
    good="\tif (!sc.nr_reclaimed) {\n\t\tcount_vm_event(LRU_KSWAPD_NO_PROGRESS);\n\t\tpgdat->kswapd_failures++;\n\t}\n"
    if bad in s: s=s.replace(bad,good,1); o.append("B1_KSWAPD=patched")
    elif good in s: o.append("B1_KSWAPD=present")
    else: die("B1 kswapd anchor unknown")
    sig="static void lru_gen_shrink_lruvec(struct lruvec *lruvec, struct scan_control *sc)"
    a,b=span(s,sig); x=s[a:b]
    bad2="\t\tif (sc->may_swap)\n\t\t\tswappiness = get_swappiness(lruvec, sc);\n\t\telse if (global_reclaim(sc) && get_swappiness(lruvec, sc))\n\t\t\tswappiness = 1;\n\t\telse\n\t\t\tswappiness = 0;\n"
    good2="\t\tif (sc->may_swap)\n\t\t\tswappiness = get_swappiness(lruvec, sc);\n\t\telse\n\t\t\tswappiness = 0;\n"
    if bad2 in x: x=x.replace(bad2,good2,1); s=s[:a]+x+s[b:]; o.append("B5_MAY_SWAP=patched")
    elif good2 in x: o.append("B5_MAY_SWAP=present")
    else: die("B5 may_swap anchor unknown")
    wr(r,p,s)

    p="mm/swap_state.c"; s=rd(r,p); sig="void clear_shadow_from_swap_cache(int type, unsigned long begin,"
    a,b=span(s,sig); x=s[a:b]
    guard="\t\t\tif (iter.index > end)\n\t\t\t\tbreak;\n"
    if guard not in x:
        anchor="\t\tradix_tree_for_each_slot(slot, &address_space->i_pages,\n\t\t\t\t\t &iter, curr) {\n"
        if x.count(anchor)!=1: die(f"B4 iterator anchor count {x.count(anchor)}")
        x=x.replace(anchor,anchor+guard,1); s=s[:a]+x+s[b:]; o.append("B4_SWAP_SHADOW=patched")
    else: o.append("B4_SWAP_SHADOW=present")
    wr(r,p,s)

def zram(r,o):
    p="drivers/block/zram/zram_drv.c"; s=rd(r,p)
    sig="static int zram_try_mark_page(struct zram *zram, u32 index)"
    a,b=span(s,sig); x=s[a:b]
    bad="""	if (!zram_allocated(zram, index) ||
			zram_test_flag(zram, index, ZRAM_UNDER_PPR)
#ifdef CONFIG_ZRAM_MULTI_COMP
			|| zram_test_flag(zram, index, ZRAM_RECOMP)
#endif
			) {
		zram_slot_unlock(zram, index);
		return ABORT;
	} else if (zram_test_flag(zram, index, ZRAM_UNDER_WB)) {
"""
    good="""	if (!zram_allocated(zram, index) ||
			zram_test_flag(zram, index, ZRAM_UNDER_PPR)) {
		zram_slot_unlock(zram, index);
		return ABORT;
	} else if (zram_test_flag(zram, index, ZRAM_UNDER_WB)
#ifdef CONFIG_ZRAM_MULTI_COMP
			|| zram_test_flag(zram, index, ZRAM_RECOMP)
#endif
			) {
"""
    if bad in x: x=x.replace(bad,good,1); s=s[:a]+x+s[b:]; o.append("D2_ZRAM_RECOMP=patched")
    elif "ZRAM_RECOMP" in x and x.find("ZRAM_RECOMP")>x.find("return ABORT;") and "return SKIP;" in x: o.append("D2_ZRAM_RECOMP=present")
    else: die("D2 zram mark-page shape unknown")
    wr(r,p,s)

def tcp(r,o):
    p="net/ipv4/tcp_output.c"; s=rd(r,p)
    sig="static void tcp_internal_pacing(struct sock *sk, const struct sk_buff *skb)"
    a,b=span(s,sig); x=s[a:b]
    if "u32 rate;" in x: x=x.replace("u32 rate;","unsigned long rate;",1)
    elif "unsigned long rate;" not in x: die("A5 TCP rate declaration unknown")
    if "do_div(len_ns, rate);" in x: x=x.replace("do_div(len_ns, rate);","len_ns = div64_ul(len_ns, rate);",1); o.append("A5_TCP_DIV64=patched")
    elif "len_ns = div64_ul(len_ns, rate);" in x: o.append("A5_TCP_DIV64=present")
    else: die("A5 TCP division anchor unknown")
    wr(r,p,s[:a]+x+s[b:])

def audit(r):
    f=rd(r,"fs/fuse/inode.c")
    if "FUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT" in f: die("audit D6 failed")
    ring=fun(rd(r,"kernel/bpf/ringbuf.c"),"static int ringbuf_map_mmap(struct bpf_map *map, struct vm_area_struct *vma)")
    if not all(v in ring for v in ("VM_WRITE","VM_MAYWRITE","return -EPERM")): die("audit BPF mmap failed")
    v=fun(rd(r,"kernel/bpf/verifier.c"),"static int check_func_arg(struct bpf_verifier_env *env, u32 regno,")
    if "ringbuf alloc_mem helper requires an unmodified reservation pointer" not in v: die("audit BPF verifier failed")
    if "arg_type == ARG_PTR_TO_ALLOC_MEM" not in v: die("audit BPF verifier scope failed")
    vm=rd(r,"mm/vmscan.c")
    if "if (!lru_gen_enabled())\n\t\t\tpgdat->kswapd_failures++;" in vm: die("audit B1 failed")
    if "else if (global_reclaim(sc) && get_swappiness(lruvec, sc))" in fun(vm,"static void lru_gen_shrink_lruvec(struct lruvec *lruvec, struct scan_control *sc)"): die("audit B5 failed")
    sh=fun(rd(r,"mm/swap_state.c"),"void clear_shadow_from_swap_cache(int type, unsigned long begin,")
    if sh.find("if (iter.index > end)")<0 or sh.find("if (iter.index > end)")>sh.find("radix_tree_iter_delete"): die("audit B4 failed")
    z=fun(rd(r,"drivers/block/zram/zram_drv.c"),"static int zram_try_mark_page(struct zram *zram, u32 index)")
    if z.find("ZRAM_RECOMP")<z.find("return ABORT;"): die("audit D2 failed")
    t=fun(rd(r,"net/ipv4/tcp_output.c"),"static void tcp_internal_pacing(struct sock *sk, const struct sk_buff *skb)")
    if "unsigned long rate;" not in t or "div64_ul(len_ns, rate)" not in t: die("audit A5 failed")

def main():
    if len(sys.argv)!=2: die(f"usage: {sys.argv[0]} <kernel-tree>")
    r=Path(sys.argv[1]).resolve()
    if not (r/"Makefile").is_file(): die("not a kernel tree")
    o=[]
    fuse(r,o); bpf(r,o); f2fs(r,o); mglru(r,o); zram(r,o); tcp(r,o); audit(r)
    print("P165 security/storage/correctness bundle: PASS")
    for x in o: print(x)
    print("fixes=D6_rollback,A2,A3,B1,B4,B5,D2,A5")
    print("scheduler_delta=none")
    print("gpu_policy_delta=none")

if __name__=="__main__": main()
