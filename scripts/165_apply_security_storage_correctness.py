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
        anchor="\t\telse if (type != expected_type)\n\t\t\tgoto err_type;\n"
        if z.count(anchor)!=1: die(f"BPF alloc_mem anchor count {z.count(anchor)}")
        add=anchor+"""\n		/* P165: ringbuf alloc_mem helper requires an unmodified reservation pointer */
		if (reg->off || !tnum_is_const(reg->var_off) ||
		    reg->var_off.value) {
			verbose(env, "R%d ringbuf alloc_mem helper requires an unmodified reservation pointer\\n", regno);
			return -EACCES;
		}
"""
        z=z.replace(anchor,add,1); x=x[:q]+z+x[e:]; s=s[:a]+x+s[b:]
        o.append("A2_BPF_OFFSET=patched")
    else: o.append("A2_BPF_OFFSET=present")
    wr(r,p,s)

def f2fs(r,o):
    p="fs/f2fs/gc.c"; s=rd(r,p)
    old="\tcpc.reason = __get_cp_reason(sbi);\n\tsbi->skipped_gc_rwsem = 0;\n\tfirst_skipped = last_skipped;\ngc_more:\n"
    new="\tcpc.reason = __get_cp_reason(sbi);\n\tfirst_skipped = last_skipped;\ngc_more:\n\tsbi->skipped_gc_rwsem = 0;\n"
    if old in s: s=s.replace(old,new,1); o.append("A3_F2FS_GC_RETRY=patched")
    elif new in s: o.append("A3_F2FS_GC_RETRY=present")
    else: die("F2FS gc_more counter anchor unknown")
    alive=fun(s,"static bool is_alive(struct f2fs_sb_info *sbi,")
    if not re.search(r"f2fs_check_nid_range\(sbi, dni->ino\).*?f2fs_put_page\(node_page, 1\).*?return false",alive,re.S): die("F2FS is_alive fix absent")
    gc=fun(s,"static int gc_data_segment(struct f2fs_sb_info *sbi,")
    if "special_file(inode->i_mode)" not in gc or gc.count("sbi->skipped_gc_rwsem++;")<2: die("F2FS stable GC fixes absent")
    cp=fun(rd(r,"fs/f2fs/checkpoint.c"),"static int __f2fs_write_meta_page(struct page *page,")
    if not all(v in cp for v in ("SBI_IS_CLOSE","ClearPageUptodate(page);","dec_page_count(sbi, F2FS_DIRTY_META);")): die("F2FS cp_error close fix absent")
    wr(r,p,s); o.append("A3_F2FS_OTHER_STABLE=already_present")

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
