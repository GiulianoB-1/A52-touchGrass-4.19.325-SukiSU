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
    cfg = root / "arch/arm64/configs/a52xq_defconfig"
    cma = root / "kernel/dma/contiguous.c"
    ion = root / "drivers/staging/android/ion/msm/msm_ion_of.c"

    for p in (cfg, cma, ion):
        if not p.is_file():
            raise SystemExit(f"missing source file: {p}")

    # Preserve the original 4 KiB kernel's physical buddy/pageblock granularity.
    #
    # P152 / 4K:
    #   PAGE_SIZE=4K, MAX_ORDER=11 -> 4K * 2^(10) = 4 MiB
    #
    # Native 16K with MAX_ORDER=11:
    #   16K * 2^(10) = 16 MiB
    #
    # Qualcomm lagoon reserved-memory was designed around 4 MiB CMA alignment
    # (qseecom, secure display, ADSP, default CMA, etc).  Keeping MAX_ORDER=11
    # therefore makes rmem_cma_setup reject valid downstream carveouts.
    #
    # Native 16K with MAX_ORDER=9 restores the same 4 MiB physical granularity:
    #   16K * 2^8 = 4 MiB
    cfg_text = cfg.read_text()
    if "CONFIG_FORCE_MAX_ZONEORDER=9" not in cfg_text:
        if "CONFIG_FORCE_MAX_ZONEORDER=11" not in cfg_text:
            raise SystemExit("a52xq_defconfig: expected CONFIG_FORCE_MAX_ZONEORDER=11")
        cfg.write_text(cfg_text.replace(
            "CONFIG_FORCE_MAX_ZONEORDER=11",
            "CONFIG_FORCE_MAX_ZONEORDER=9",
            1,
        ))
        print(f"{cfg}: set CONFIG_FORCE_MAX_ZONEORDER=9")
    else:
        print(f"{cfg}: CONFIG_FORCE_MAX_ZONEORDER=9 already set")

    replace_once(
        cma,
        """static int __init rmem_cma_setup(struct reserved_mem *rmem)
{
	phys_addr_t align = PAGE_SIZE << max(MAX_ORDER - 1, pageblock_order);
	phys_addr_t mask = align - 1;
	unsigned long node = rmem->fdt_node;
""",
        """static int __init rmem_cma_setup(struct reserved_mem *rmem)
{
	phys_addr_t align = PAGE_SIZE << max(MAX_ORDER - 1, pageblock_order);
	phys_addr_t mask = align - 1;
	unsigned long node = rmem->fdt_node;

	pr_info_once("A52 P380 CMA_GEOM: PAGE_SIZE=%lu MAX_ORDER=%d pageblock_order=%u required_align=%pa\\n",
		     (unsigned long)PAGE_SIZE, MAX_ORDER, pageblock_order, &align);
""",
        "P380 CMA runtime geometry marker",
    )

    replace_once(
        ion,
        """		if (!ret) {
			heap->base = base;
			heap->size = size;
		}
		of_node_put(pnode);
""",
        """		if (!ret) {
			heap->base = base;
			heap->size = size;
			pr_info("A52 P380 ION_DT_OK: heap=%s base=%pa size=%pa PAGE_SIZE=%lu\\n",
				heap->name, &heap->base, &heap->size,
				(unsigned long)PAGE_SIZE);
		} else {
			pr_err("A52 P380 ION_DT_FAIL: heap=%s region=%pOF PAGE_SIZE=%lu rc=%d\\n",
			       heap->name, pnode, (unsigned long)PAGE_SIZE, ret);
		}
		of_node_put(pnode);
""",
        "P380 ION DT result diagnostics",
    )

    # Static postconditions.
    if "CONFIG_FORCE_MAX_ZONEORDER=9" not in cfg.read_text():
        raise SystemExit("P380 final config request missing")
    if "A52 P380 CMA_GEOM" not in cma.read_text():
        raise SystemExit("P380 CMA marker missing")
    if "A52 P380 ION_DT_OK" not in ion.read_text():
        raise SystemExit("P380 ION diagnostics missing")

    print("A52 Phase380 16K CMA/pageblock compatibility applied successfully")

if __name__ == "__main__":
    main()
