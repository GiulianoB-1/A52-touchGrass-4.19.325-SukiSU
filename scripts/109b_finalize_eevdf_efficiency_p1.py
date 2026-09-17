#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 109b_finalize_eevdf_efficiency_p1.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
fair = root / "kernel/sched/fair.c"
if not fair.is_file():
    raise SystemExit(f"missing fair.c: {fair}")

text = fair.read_text()
marker = "/* EEVDF phase 4 augmented deadline tree. */"
decl = "static inline u64 eevdf_base_slice(void);\n\n"

if marker not in text:
    raise SystemExit("EEVDF phase4 marker missing")
if decl.strip() not in text[:text.index(marker) + len(marker)]:
    text = text.replace(marker, decl + marker, 1)

# The later definition from Phase77 must still exist exactly once.
if text.count("static inline u64 eevdf_base_slice(void)") != 2:
    raise SystemExit(
        "expected one forward declaration plus one eevdf_base_slice definition"
    )

fair.write_text(text)
print("EEVDF efficiency helper ordering finalized")
print("eevdf_base_slice_forward_decl=present")
