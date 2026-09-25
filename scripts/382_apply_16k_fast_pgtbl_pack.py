#!/usr/bin/env python3
from pathlib import Path
import sys

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    pg = root / "drivers/iommu/io-pgtable-fast.c"
    if not pg.is_file():
        raise SystemExit(f"missing source file: {pg}")

    replace_once(
        pg,
        """/*
 * We need max 1 page for the pgd, 4 pages for puds (1GB VA per pud page) and
 * 2048 pages for pmds (each pud page contains 512 table entries, each
 * pointing to a pmd).
 */
#define NUM_PGD_PAGES 1
#define NUM_PUD_PAGES 4
#define NUM_PMD_PAGES 2048
#define NUM_PGTBL_PAGES (NUM_PGD_PAGES + NUM_PUD_PAGES + NUM_PMD_PAGES)
""",
        """/*
 * The fast SMMU format is permanently 4 KiB-granular: one PMD/PTE table is
 * 512 x 8-byte entries = 4 KiB.  Linux PAGE_SIZE may be larger.  On a 16 KiB
 * kernel we therefore pack four 4 KiB hardware tables into each Linux page,
 * preserving the driver's flat pmds[] view and the hardware's 4 KiB table
 * addresses without wasting 12 KiB between every table.
 *
 * NUM_PMD_PAGES is kept as the worst-case 4 KiB-page allocation count for the
 * pointer-array capacity; actual allocated Linux pages may be fewer.
 */
#define NUM_PGD_PAGES 1
#define NUM_PUD_PAGES 4
#define NUM_PMD_PAGES 2048
#define NUM_PGTBL_PAGES (NUM_PGD_PAGES + NUM_PUD_PAGES + NUM_PMD_PAGES)
#define AV8L_FAST_TABLE_SIZE SZ_4K
#define AV8L_FAST_TABLES_PER_PAGE (PAGE_SIZE / AV8L_FAST_TABLE_SIZE)
""",
        "P382 table packing constants",
    )

    replace_once(
        pg,
        """	int i, j, pg = 0;
	struct page **pages, *page;
	dma_addr_t base = cfg->iova_base;
	dma_addr_t end = cfg->iova_end;
	dma_addr_t pud, pmd;
	int pmd_pg_index;
""",
        """	int i, j, pg = 0;
	struct page **pages, *page;
	dma_addr_t base = cfg->iova_base;
	dma_addr_t end = cfg->iova_end;
	dma_addr_t pud, pmd;
	int pmd_pg_index;
	unsigned int pmd_table_nr = 0;
	struct page *pmd_backing_page = NULL;

	BUILD_BUG_ON(PAGE_SIZE < AV8L_FAST_TABLE_SIZE);
	BUILD_BUG_ON(PAGE_SIZE % AV8L_FAST_TABLE_SIZE);
	pr_info_once("A52 P382 FAST_PGTBL_PACK: PAGE_SIZE=%lu table_size=%u tables_per_page=%lu\\n",
		     (unsigned long)PAGE_SIZE, AV8L_FAST_TABLE_SIZE,
		     (unsigned long)AV8L_FAST_TABLES_PER_PAGE);
""",
        "P382 runtime geometry marker",
    )

    old_loop="""	/*
	 * We have max 4 puds, each of which can point to 512 pmds, so we'll
	 * have max 2048 pmds, each of which can hold 512 ptes, for a grand
	 * total of 2048*512=1048576 PTEs.
	 */
	pmd_pg_index = pg;
	for (i = pud_index(base), pud = base; pud < end;
			++i, pud = pud_next(pud, end)) {
		for (j = pmd_index(pud), pmd = pud; pmd < pud_next(pud, end);
				++j, pmd = pmd_next(pmd, end)) {
			av8l_fast_iopte pte, *pudp;
			void *addr;

			page = alloc_page(GFP_KERNEL | __GFP_ZERO);
			if (!page)
				goto err_free_pages;
			pages[pg++] = page;

			addr = page_address(page);
			dmac_clean_range(addr, addr + SZ_4K);

			pte = page_to_phys(page) | AV8L_FAST_PTE_TYPE_TABLE;
			pudp = data->puds[i] + j;
			*pudp = pte;
		}
		dmac_clean_range(data->puds[i], data->puds[i] + 512);
	}
"""
    new_loop="""	/*
	 * We have max 4 puds, each of which can point to 512 pmds, so we'll
	 * have max 2048 hardware PMD tables.  Each hardware table is exactly
	 * 4 KiB even when Linux PAGE_SIZE is 16 KiB.  Pack consecutive 4 KiB
	 * tables into each Linux page so vmap() below still presents a truly
	 * contiguous 4 KiB-table array to iopte_pmd_offset().
	 */
	pmd_pg_index = pg;
	for (i = pud_index(base), pud = base; pud < end;
			++i, pud = pud_next(pud, end)) {
		for (j = pmd_index(pud), pmd = pud; pmd < pud_next(pud, end);
				++j, pmd = pmd_next(pmd, end)) {
			av8l_fast_iopte pte, *pudp;
			unsigned int slot =
				pmd_table_nr % AV8L_FAST_TABLES_PER_PAGE;
			unsigned long table_off =
				(unsigned long)slot * AV8L_FAST_TABLE_SIZE;
			void *addr;

			if (!slot) {
				pmd_backing_page =
					alloc_page(GFP_KERNEL | __GFP_ZERO);
				if (!pmd_backing_page)
					goto err_free_pages;
				pages[pg++] = pmd_backing_page;
			}

			page = pmd_backing_page;
			addr = page_address(page) + table_off;
			dmac_clean_range(addr, addr + AV8L_FAST_TABLE_SIZE);

			pte = (page_to_phys(page) + table_off) |
				AV8L_FAST_PTE_TYPE_TABLE;
			pudp = data->puds[i] + j;
			*pudp = pte;
			pmd_table_nr++;
		}
		dmac_clean_range(data->puds[i], data->puds[i] + 512);
	}
"""
    replace_once(pg, old_loop, new_loop, "P382 pack 4K PMD tables inside Linux pages")

    replace_once(
        pg,
        """	data->pmds = vmap(&pages[pmd_pg_index], pg - pmd_pg_index,
			  VM_IOREMAP, PAGE_KERNEL);
	if (!data->pmds)
		goto err_free_pages;
""",
        """	data->pmds = vmap(&pages[pmd_pg_index], pg - pmd_pg_index,
			  VM_IOREMAP, PAGE_KERNEL);
	if (!data->pmds)
		goto err_free_pages;

	pr_info("A52 P382 FAST_PGTBL_READY: hw_tables=%u linux_pages=%d pmds=%pK\\n",
		pmd_table_nr, pg - pmd_pg_index, data->pmds);
""",
        "P382 table-ready marker",
    )

    text = pg.read_text()
    checks = [
        "AV8L_FAST_TABLES_PER_PAGE",
        "A52 P382 FAST_PGTBL_PACK:",
        "A52 P382 FAST_PGTBL_READY:",
        "page_to_phys(page) + table_off",
        "pmd_table_nr++",
    ]
    for marker in checks:
        if marker not in text:
            raise SystemExit(f"postcondition missing: {marker}")

    print("A52 Phase382 packed 4K fast-SMMU page-table compatibility applied successfully")

if __name__ == "__main__":
    main()
