#!/usr/bin/env python3
from pathlib import Path
import sys

MARK = "A52_PHASE391_EXACT_RAMOOPS_ALIAS_DEDUP_V1"

def replace_once(path, old, new, label):
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()
    src = root / "drivers/of/of_reserved_mem.c"
    if not src.is_file():
        raise SystemExit(f"missing {src}")

    anchor = """static bool rmem_overlap;
static void __init __rmem_check_for_overlap(void)
{
"""
    patch = r'''static bool rmem_overlap;

/*
 * A52_PHASE391_EXACT_RAMOOPS_ALIAS_DEDUP_V1
 *
 * Phase390 hardware capture exposed a contaminated legacy boot DT containing
 * two aliases for the exact same proven 1 MiB ramoops reservation:
 *
 *   ramoops@B1B00000                 0xb1b00000..0xb1c00000
 *   a52-ramoops-reserved@b1b00000    0xb1b00000..0xb1c00000
 *
 * Samsung's downstream reserved-memory checker correctly treats arbitrary
 * overlaps as fatal at late_initcall time.  Preserve that policy.  The only
 * exception here is this exact same-base/same-size ramoops alias pair, which
 * came from the old GKI diagnostic boot metadata and does not describe two
 * distinct physical allocations.
 */
static bool __init a52_p391_same_ramoops_alias(
		const struct reserved_mem *a, const struct reserved_mem *b)
{
	bool a_stock, b_stock, a_alias, b_alias;

	if (!a || !b || !a->name || !b->name)
		return false;

	if (a->base != 0xB1B00000ULL || b->base != 0xB1B00000ULL ||
	    a->size != SZ_1M || b->size != SZ_1M)
		return false;

	a_stock = !strcmp(a->name, "ramoops@B1B00000") ||
		  !strcmp(a->name, "ramoops@b1b00000");
	b_stock = !strcmp(b->name, "ramoops@B1B00000") ||
		  !strcmp(b->name, "ramoops@b1b00000");
	a_alias = !strcmp(a->name, "a52-ramoops-reserved@b1b00000") ||
		  !strcmp(a->name, "a52-ramoops-reserved@B1B00000");
	b_alias = !strcmp(b->name, "a52-ramoops-reserved@b1b00000") ||
		  !strcmp(b->name, "a52-ramoops-reserved@B1B00000");

	return (a_stock && b_alias) || (a_alias && b_stock);
}

static void __init __rmem_check_for_overlap(void)
{
'''
    replace_once(src, anchor, patch, "exact duplicate ramoops alias helper")

    old = r'''		if (this->base + this->size > next->base) {
			phys_addr_t this_end, next_end;

			this_end = this->base + this->size;
			next_end = next->base + next->size;
			pr_err("OVERLAP DETECTED!
%s (%pa--%pa) overlaps with %s (%pa--%pa)
",
			       this->name, &this->base, &this_end,
			       next->name, &next->base, &next_end);
			rmem_overlap = true;
		}
'''
    new = r'''		if (this->base + this->size > next->base) {
			phys_addr_t this_end, next_end;

			this_end = this->base + this->size;
			next_end = next->base + next->size;

			if (a52_p391_same_ramoops_alias(this, next)) {
				pr_warn("A52 P391 RMEM_ALIAS: exact duplicate %s/%s %pa--%pa ignored
",
					this->name, next->name,
					&this->base, &this_end);
				continue;
			}

			pr_err("OVERLAP DETECTED!
%s (%pa--%pa) overlaps with %s (%pa--%pa)
",
			       this->name, &this->base, &this_end,
			       next->name, &next->base, &next_end);
			rmem_overlap = true;
		}
'''
    replace_once(src, old, new, "skip only exact ramoops alias overlap")

    text = src.read_text()
    for token in (
        MARK,
        "a52_p391_same_ramoops_alias",
        "A52 P391 RMEM_ALIAS:",
        "0xB1B00000ULL",
        "a52-ramoops-reserved@b1b00000",
        'panic("overlap on reserved memory, check the latest change")',
    ):
        if token not in text:
            raise SystemExit(f"Phase391 postcondition missing: {token}")

    print("A52 Phase391 exact ramoops alias dedup applied successfully")

if __name__ == "__main__":
    main()
