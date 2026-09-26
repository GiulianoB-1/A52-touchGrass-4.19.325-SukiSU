#!/usr/bin/env python3
from pathlib import Path
import sys

def replace_once(path, old, new, label):
    text=path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    c=text.count(old)
    if c!=1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {c}")
    path.write_text(text.replace(old,new,1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv)!=2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    p=root/"drivers/iommu/io-pgtable-fast.c"
    if not p.is_file():
        raise SystemExit(f"missing {p}")

    replace_once(
        p,
        "#define iopte_pmd_offset(pmds, base, iova) (pmds + ((iova - base) >> 12))\n",
        """/*
 * A52 P382: AV8L hardware always uses 4K PTE tables (512 x 8-byte entries),
 * but alloc_page() returns PAGE_SIZE bytes.  With native 16K Linux pages,
 * consecutive preallocated hardware PTE tables are 16K apart in the vmap,
 * not 4K apart.  Keep the hardware PTE index at 4K granularity while using
 * PAGE_SIZE as the backing-table virtual stride.
 */
#define AV8L_FAST_PTES_PER_TABLE	512UL
#define AV8L_FAST_TABLE_SHIFT		21
#define iopte_pmd_offset(pmds, base, iova) \
	((av8l_fast_iopte *)((char *)(pmds) + \
	 ((((unsigned long)(iova) >> AV8L_FAST_TABLE_SHIFT) - \
	   ((unsigned long)(base) >> AV8L_FAST_TABLE_SHIFT)) * PAGE_SIZE)) + \
	 ((((unsigned long)(iova)) >> AV8L_FAST_PAGE_SHIFT) & \
	  (AV8L_FAST_PTES_PER_TABLE - 1)))
""",
        "16K backing-page aware fast PTE table stride",
    )

    replace_once(
        p,
        """static int av8l_fast_map(struct io_pgtable_ops *ops, unsigned long iova,
			 phys_addr_t paddr, size_t size, int prot)
{
	struct av8l_fast_io_pgtable *data = iof_pgtable_ops_to_data(ops);
	av8l_fast_iopte *ptep = iopte_pmd_offset(data->pmds, data->base, iova);
	unsigned long i, nptes = size >> AV8L_FAST_PAGE_SHIFT;
""",
        """static int av8l_fast_map(struct io_pgtable_ops *ops, unsigned long iova,
			 phys_addr_t paddr, size_t size, int prot)
{
	struct av8l_fast_io_pgtable *data = iof_pgtable_ops_to_data(ops);
	av8l_fast_iopte *ptep = iopte_pmd_offset(data->pmds, data->base, iova);
	unsigned long i, nptes = size >> AV8L_FAST_PAGE_SHIFT;

	pr_info_once("A52 P382 FAST_PGTBL_MAP: PAGE_SIZE=%lu base=%#llx iova=%#lx ptep=%p table=%lu entry=%lu\\n",
		     (unsigned long)PAGE_SIZE, (unsigned long long)data->base,
		     iova, ptep,
		     (((unsigned long)iova >> AV8L_FAST_TABLE_SHIFT) -
		      ((unsigned long)data->base >> AV8L_FAST_TABLE_SHIFT)),
		     (((unsigned long)iova >> AV8L_FAST_PAGE_SHIFT) &
		      (AV8L_FAST_PTES_PER_TABLE - 1)));
""",
        "first fast PTE map geometry trace",
    )

    replace_once(
        p,
        """static size_t
__av8l_fast_unmap(struct io_pgtable_ops *ops, unsigned long iova,
			size_t size, bool allow_stale_tlb)
{
	struct av8l_fast_io_pgtable *data = iof_pgtable_ops_to_data(ops);
	unsigned long nptes;
	av8l_fast_iopte *ptep;
""",
        """static size_t
__av8l_fast_unmap(struct io_pgtable_ops *ops, unsigned long iova,
			size_t size, bool allow_stale_tlb)
{
	struct av8l_fast_io_pgtable *data = iof_pgtable_ops_to_data(ops);
	unsigned long nptes;
	av8l_fast_iopte *ptep;
""",
        "unmap anchor",
    )

    replace_once(
        p,
        """	ptep = iopte_pmd_offset(data->pmds, data->base, iova);
	nptes = size >> AV8L_FAST_PAGE_SHIFT;

	memset(ptep, val, sizeof(*ptep) * nptes);
""",
        """	ptep = iopte_pmd_offset(data->pmds, data->base, iova);
	nptes = size >> AV8L_FAST_PAGE_SHIFT;

	pr_info_once("A52 P382 FAST_PGTBL_UNMAP: PAGE_SIZE=%lu base=%#llx iova=%#lx ptep=%p table=%lu entry=%lu nptes=%lu\\n",
		     (unsigned long)PAGE_SIZE, (unsigned long long)data->base,
		     iova, ptep,
		     (((unsigned long)iova >> AV8L_FAST_TABLE_SHIFT) -
		      ((unsigned long)data->base >> AV8L_FAST_TABLE_SHIFT)),
		     (((unsigned long)iova >> AV8L_FAST_PAGE_SHIFT) &
		      (AV8L_FAST_PTES_PER_TABLE - 1)), nptes);

	memset(ptep, val, sizeof(*ptep) * nptes);
""",
        "first fast PTE unmap geometry trace",
    )

    text=p.read_text()
    for marker in [
        "A52 P382 FAST_PGTBL_MAP",
        "A52 P382 FAST_PGTBL_UNMAP",
        "AV8L_FAST_PTES_PER_TABLE",
        "* PAGE_SIZE",
    ]:
        if marker not in text:
            raise SystemExit(f"postcondition missing: {marker}")
    print("A52 Phase382 fast io-pgtable 16K stride compatibility applied successfully")

if __name__=="__main__":
    main()
