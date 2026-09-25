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
    p=root/"drivers/iommu/dma-mapping-fast.c"
    if not p.is_file():
        raise SystemExit(f"missing {p}")

    replace_once(
        p,
        "#define FAST_PAGE_MASK (~(PAGE_SIZE - 1))\n",
        "#define FAST_PAGE_MASK (~(FAST_PAGE_SIZE - 1)) /* A52 P381: IOMMU granule is fixed 4K, independent of Linux PAGE_SIZE */\n",
        "FAST_PAGE_MASK uses 4K IOMMU granule",
    )

    replace_once(
        p,
        """static void *fast_smmu_alloc_atomic(struct dma_fast_smmu_mapping *mapping,
				    size_t size, gfp_t gfp, unsigned long attrs,
				    dma_addr_t *handle, bool coherent)
{
	void *addr;
	unsigned long flags;
	struct page *page;
	dma_addr_t dma_addr;
	int prot = dma_info_to_prot(DMA_BIDIRECTIONAL, coherent, attrs);

	if (coherent) {
		page = alloc_pages(gfp, get_order(size));
		addr = page ? page_address(page) : NULL;
	} else
		addr = __alloc_from_pool(size, &page, gfp);
""",
        """static void *fast_smmu_alloc_atomic(struct dma_fast_smmu_mapping *mapping,
				    size_t size, gfp_t gfp, unsigned long attrs,
				    dma_addr_t *handle, bool coherent)
{
	void *addr;
	unsigned long flags;
	struct page *page;
	dma_addr_t dma_addr;
	size_t cpu_size = PAGE_ALIGN(size);
	int prot = dma_info_to_prot(DMA_BIDIRECTIONAL, coherent, attrs);

	if (coherent) {
		page = alloc_pages(gfp, get_order(cpu_size));
		addr = page ? page_address(page) : NULL;
	} else
		addr = __alloc_from_pool(cpu_size, &page, gfp);
""",
        "atomic CPU allocation uses Linux page size",
    )

    replace_once(
        p,
        """out_free_page:
	coherent ? __free_pages(page, get_order(size)) :
		   __free_from_pool(addr, size);
	return NULL;
}
""",
        """out_free_page:
	coherent ? __free_pages(page, get_order(cpu_size)) :
		   __free_from_pool(addr, cpu_size);
	return NULL;
}
""",
        "atomic CPU free uses Linux page size",
    )

    replace_once(
        p,
        """	page = dma_alloc_from_contiguous(dev, size >> PAGE_SHIFT,
					get_order(size), gfp & __GFP_NOWARN);
""",
        """	page = dma_alloc_from_contiguous(dev, PAGE_ALIGN(size) >> PAGE_SHIFT,
					get_order(PAGE_ALIGN(size)), gfp & __GFP_NOWARN);
""",
        "contiguous allocation count uses Linux pages",
    )

    replace_once(
        p,
        """	coherent_addr = dma_common_contiguous_remap(page, size, VM_USERMAP,
				remap_prot, __fast_smmu_alloc_contiguous);
""",
        """	coherent_addr = dma_common_contiguous_remap(page, PAGE_ALIGN(size),
				VM_USERMAP, remap_prot, __fast_smmu_alloc_contiguous);
""",
        "contiguous CPU remap spans Linux pages",
    )

    replace_once(
        p,
        """release_page:
	dma_release_from_contiguous(dev, page, size >> PAGE_SHIFT);
	return NULL;
}
""",
        """release_page:
	dma_release_from_contiguous(dev, page, PAGE_ALIGN(size) >> PAGE_SHIFT);
	return NULL;
}
""",
        "contiguous release count uses Linux pages",
    )

    replace_once(
        p,
        """	size_t count = ALIGN(size, SZ_4K) >> PAGE_SHIFT;
""",
        """	size_t count = PAGE_ALIGN(size) >> PAGE_SHIFT; /* A52 P381: count Linux struct pages, not 4K IOMMU PTEs */
""",
        "page-array count uses Linux pages",
    )

    replace_once(
        p,
        """	addr = dma_common_pages_remap(pages, size, VM_USERMAP, remap_prot,
				      __builtin_return_address(0));
""",
        """	addr = dma_common_pages_remap(pages, PAGE_ALIGN(size), VM_USERMAP,
				      remap_prot, __builtin_return_address(0));
""",
        "CPU remap size is Linux-page aligned",
    )

    replace_once(
        p,
        """static void fast_smmu_free(struct device *dev, size_t size,
			   void *cpu_addr, dma_addr_t dma_handle,
			   unsigned long attrs)
{
	struct dma_fast_smmu_mapping *mapping = dev_get_mapping(dev);
	struct vm_struct *area;
	unsigned long flags;

	size = ALIGN(size, FAST_PAGE_SIZE);
""",
        """static void fast_smmu_free(struct device *dev, size_t size,
			   void *cpu_addr, dma_addr_t dma_handle,
			   unsigned long attrs)
{
	struct dma_fast_smmu_mapping *mapping = dev_get_mapping(dev);
	struct vm_struct *area;
	unsigned long flags;
	size_t cpu_size = PAGE_ALIGN(size);

	size = ALIGN(size, FAST_PAGE_SIZE);
""",
        "track CPU page-aligned free size",
    )

    replace_once(
        p,
        """		dma_common_free_remap(cpu_addr, size, VM_USERMAP, false);
		__fast_smmu_free_pages(pages, size >> FAST_PAGE_SHIFT);
	} else if (attrs & DMA_ATTR_FORCE_CONTIGUOUS) {
		struct page *page = vmalloc_to_page(cpu_addr);

		dma_common_free_remap(cpu_addr, size, VM_USERMAP, false);
		dma_release_from_contiguous(dev, page, size >> PAGE_SHIFT);
	} else if (!is_vmalloc_addr(cpu_addr)) {
		__free_pages(virt_to_page(cpu_addr), get_order(size));
	} else if (__in_atomic_pool(cpu_addr, size)) {
		// Keep remap
		__free_from_pool(cpu_addr, size);
""",
        """		dma_common_free_remap(cpu_addr, cpu_size, VM_USERMAP, false);
		__fast_smmu_free_pages(pages, cpu_size >> PAGE_SHIFT);
	} else if (attrs & DMA_ATTR_FORCE_CONTIGUOUS) {
		struct page *page = vmalloc_to_page(cpu_addr);

		dma_common_free_remap(cpu_addr, cpu_size, VM_USERMAP, false);
		dma_release_from_contiguous(dev, page, cpu_size >> PAGE_SHIFT);
	} else if (!is_vmalloc_addr(cpu_addr)) {
		__free_pages(virt_to_page(cpu_addr), get_order(cpu_size));
	} else if (__in_atomic_pool(cpu_addr, cpu_size)) {
		// Keep remap
		__free_from_pool(cpu_addr, cpu_size);
""",
        "free CPU backing by Linux page size",
    )

    # Add one runtime marker near mapping init so ramoops proves the two granules.
    replace_once(
        p,
        """int fast_smmu_init_mapping(struct device *dev,
			    struct dma_iommu_mapping *mapping)
{
	int err = 0;
""",
        """int fast_smmu_init_mapping(struct device *dev,
			    struct dma_iommu_mapping *mapping)
{
	int err = 0;

	pr_info_once("A52 P381 FAST_SMMU_GEOM: PAGE_SIZE=%lu FAST_PAGE_SIZE=%lu PAGE_SHIFT=%d FAST_PAGE_SHIFT=%d\\n",
		     (unsigned long)PAGE_SIZE, (unsigned long)FAST_PAGE_SIZE,
		     PAGE_SHIFT, FAST_PAGE_SHIFT);
""",
        "runtime fast-SMMU geometry marker",
    )

    text=p.read_text()
    checks=[
        "#define FAST_PAGE_MASK (~(FAST_PAGE_SIZE - 1))",
        "size_t count = PAGE_ALIGN(size) >> PAGE_SHIFT",
        "size_t cpu_size = PAGE_ALIGN(size);",
        "A52 P381 FAST_SMMU_GEOM",
    ]
    for c in checks:
        if c not in text:
            raise SystemExit(f"postcondition missing: {c}")
    print("A52 Phase381 fast-SMMU 16K compatibility applied successfully")

if __name__=="__main__":
    main()
