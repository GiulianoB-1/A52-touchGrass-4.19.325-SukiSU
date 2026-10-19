#!/usr/bin/env python3
"""P171: deduplicate exactly one leftover GKI-78 ramoops reservation on TG4.19.

The P171 hardware capture shows:
  ramoops@B1B00000                    0xb1b00000..0xb1c00000
  a52-ramoops-reserved@b1b00000       0xb1b00000..0xb1c00000

The latter is a reservation-only node created by the historical GKI
workflow 78 DTB repack. A TG4.19 late_initcall panics on this early-detected
overlap. Preserve the canonical ramoops and all other overlap validation.
This source patch is P171-only, after the P170 baseline build and before
the BakaSU full kernel rebuild.
"""
from pathlib import Path
import hashlib
import sys

def single_replace(s: str, old: str, new: str, why: str) -> str:
    found = s.count(old)
    if found != 1:
        raise RuntimeError(f"{why}: expected one original anchor, got {found}")
    return s.replace(old, new, 1)

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: 171_fix_ramoops_reserved_alias.py <kernel_root>")
    root = Path(sys.argv[1]).resolve()
    p = root / "drivers/of/of_reserved_mem.c"
    s = p.read_text()
    if "A52_P171_RMEM_ALIAS" in s:
        raise RuntimeError("P171 patch already applied, not silently reapplying")
    for marker in (
        'panic("overlap on reserved memory, check the latest change")',
        'static bool rmem_overlap;',
        'static void __init __rmem_check_for_overlap(void)',
        'void __init fdt_init_reserved_mem(void)',
    ):
        if s.count(marker) != 1:
            raise RuntimeError(f"Expected stock TouchGrass overlap check: {marker}")
    old_sha=hashlib.sha256(s.encode()).hexdigest()
    s=single_replace(s,
        "#include <linux/kmemleak.h>\n",
        "#include <linux/kmemleak.h>\n#include <linux/string.h>\n",
        "string header")

    # This is NOT a generic "ignore reserved memory overlaps" patch.
    # Its scope is one exactly identified alias with identical base and
    # size. The normal overlap panic for everything else remains intact.
    helper=r"""/*
 * A52 P171: the boot DTB from the GKI ramoops diagnostic can contain
 * both the legacy ramoops node and its later reservation-only alias.
 * The alias reserves precisely the same 1 MiB; keeping both makes
 * check_reserved_mem() panic after late_initcall.
 *
 * Never hide a partial overlap, different-sized node, or any other
 * reservation. Keep the normal ramoops node to retain crash logging.
 */
static void __init __rmem_dedupe_a52_ramoops_alias(void)
{
	int normal = -1, alias = -1, i;

	for (i = 0; i < reserved_mem_count; i++) {
		struct reserved_mem *rmem = &reserved_mem[i];

		if (rmem->base != (phys_addr_t)0xb1b00000 ||
		    rmem->size != SZ_1M)
			continue;

		if (!strcmp(rmem->name, "ramoops@B1B00000"))
			normal = i;
		else if (!strcmp(rmem->name, "a52-ramoops-reserved@b1b00000"))
			alias = i;
	}

	if (normal < 0 || alias < 0)
		return;

	pr_warn("A52_P171_RMEM_ALIAS: discarding duplicate reservation-only node %s; keeping ramoops@B1B00000\n",
		reserved_mem[alias].name);
	memmove(&reserved_mem[alias], &reserved_mem[alias + 1],
		(reserved_mem_count - alias - 1) * sizeof(reserved_mem[0]));
	reserved_mem_count--;
}

"""
    s=single_replace(s,
        "static bool rmem_overlap;\n",
        helper + "static bool rmem_overlap;\n",
        "reserved-memory alias helper")
    needle="\tsort(reserved_mem, reserved_mem_count, sizeof(reserved_mem[0]),\n\t     __rmem_cmp, NULL);\n"
    s=single_replace(s,needle,
        needle + "\t__rmem_dedupe_a52_ramoops_alias();\n",
        "invoke alias removal before overlap detection")

    # Validation: the old fatal guard remains in place, and only the two
    # known memory nodes and their exact allocation are whitelisted.
    assert s.count("A52_P171_RMEM_ALIAS") == 1
    assert s.count("panic(\"overlap on reserved memory, check the latest change\")") == 1
    assert s.count("__rmem_dedupe_a52_ramoops_alias();") == 1
    assert s.count("reserved_mem_count--;") == 1
    assert "0xb1b00000" in s and "SZ_1M" in s
    assert '"a52-ramoops-reserved@b1b00000"' in s
    assert '"ramoops@B1B00000"' in s
    p.write_text(s)
    new_sha=hashlib.sha256(s.encode()).hexdigest()
    print("P171_RMEM_ALIAS exact_duplicate=ramoops@B1B00000+a52-ramoops-reserved@b1b00000")
    print("P171_RMEM_ALIAS base=0xB1B00000 size=0x100000")
    print("P171_RMEM_ALIAS remaining_overlap_panic=ENABLED")
    print("P171_RMEM_ALIAS source_sha256_before="+old_sha)
    print("P171_RMEM_ALIAS source_sha256_after="+new_sha)

if __name__=="__main__":
    main()
