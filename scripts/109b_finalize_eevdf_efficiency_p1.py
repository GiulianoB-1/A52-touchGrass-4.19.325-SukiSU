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
base_decl = "static inline u64 eevdf_base_slice(void);\n"
delta_decl = "static inline u64 calc_delta_fair(u64 delta, struct sched_entity *se);\n"
decls = base_decl + delta_decl + "\n"

if marker not in text:
    raise SystemExit("EEVDF phase4 marker missing")
marker_pos = text.index(marker)

# Phase109's augmented-tree/protection helpers are emitted before the vendor
# fair.c definition of calc_delta_fair(). Declare both helpers they depend on
# before that generated region so Clang never creates an implicit extern
# declaration and then rejects the later static inline definition.
if base_decl.strip() not in text[:marker_pos]:
    text = text.replace(marker, base_decl + marker, 1)
if delta_decl.strip() not in text[:text.index(marker)]:
    text = text.replace(marker, delta_decl + marker, 1)

# The 109 generator intentionally keeps the helper text readable as a raw
# Python block. Normalize any literal backslash-t tokens in that generated
# helper range into real C indentation before compilation.
start = text.find("static inline u64 eevdf_cfs_rq_min_slice(")
end = text.find("static struct sched_entity *eevdf_pick_entity(", start)
if start < 0 or end < 0:
    raise SystemExit("could not bound generated EEVDF protection helpers")
helper = text[start:end].replace("\\t", "\t")
text = text[:start] + helper + text[end:]

# Both forward declarations must be before the protection helpers. The later
# Phase77/base implementation and vendor calc_delta_fair() definitions must
# remain exactly once each.
start = text.find("static inline u64 eevdf_cfs_rq_min_slice(")
if text.find(base_decl.strip()) < 0 or text.find(base_decl.strip()) >= start:
    raise SystemExit("eevdf_base_slice declaration is not before protection helpers")
if text.find(delta_decl.strip()) < 0 or text.find(delta_decl.strip()) >= start:
    raise SystemExit("calc_delta_fair declaration is not before protection helpers")
if text.count("static inline u64 eevdf_base_slice(void)") != 2:
    raise SystemExit(
        "expected one forward declaration plus one eevdf_base_slice definition"
    )
if text.count("static inline u64 calc_delta_fair(u64 delta, struct sched_entity *se)") != 2:
    raise SystemExit(
        "expected one forward declaration plus one calc_delta_fair definition"
    )
if "\\t" in text[start:start + len(helper)]:
    raise SystemExit("literal backslash-t remains in EEVDF helper C source")

fair.write_text(text)
print("EEVDF efficiency helper ordering finalized")
print("eevdf_base_slice_forward_decl=present")
print("calc_delta_fair_forward_decl=present")
print("generated_helper_tabs=sanitized")
