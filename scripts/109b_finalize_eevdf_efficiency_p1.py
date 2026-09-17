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

# The 109 generator intentionally keeps the helper text readable as a raw
# Python block. Normalize any literal backslash-t tokens in that generated
# helper range into real C indentation before compilation.
start = text.find("static inline u64 eevdf_cfs_rq_min_slice(")
end = text.find("static struct sched_entity *eevdf_pick_entity(", start)
if start < 0 or end < 0:
    raise SystemExit("could not bound generated EEVDF protection helpers")
helper = text[start:end].replace("\\t", "\t")
text = text[:start] + helper + text[end:]

# The later definition from Phase77 must still exist exactly once in addition
# to the forward declaration needed by the earlier augmented-tree helper.
if text.count("static inline u64 eevdf_base_slice(void)") != 2:
    raise SystemExit(
        "expected one forward declaration plus one eevdf_base_slice definition"
    )
if "\\t" in text[start:start + len(helper)]:
    raise SystemExit("literal backslash-t remains in EEVDF helper C source")

fair.write_text(text)
print("EEVDF efficiency helper ordering finalized")
print("eevdf_base_slice_forward_decl=present")
print("generated_helper_tabs=sanitized")
