#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
REPORT="$ROOT/artifacts/phase102-419236-compile-repair.txt"

# Keep all previously proven Phase102 compile repairs unchanged, then apply
# the next narrow Samsung/stable reconciliation discovered by the compiler.
"$SCRIPT_DIR/102_fix_419236_compile_base.sh"

python3 - "$KERNEL" "$REPORT" <<'PY'
from pathlib import Path
import re
import sys

kernel = Path(sys.argv[1])
report = Path(sys.argv[2])


def function_end(source: str, start: int) -> int:
    brace = source.find("{", start)
    if brace < 0:
        raise SystemExit("function opening brace missing")
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise SystemExit("function closing brace missing")


# Samsung's dvb_dmxdev_init() carries capability probing for
# DMX_CAP_AUTO_BUFFER_FLUSH and stores the result in a local struct dmx_caps.
# Linux stable changed nearby initialization code. The .235 policy merge kept
# Samsung's capability block but lost only the local `caps` declaration,
# producing two undeclared-identifier errors. Restore that declaration while
# refusing to touch an unfamiliar function shape or remove vendor behavior.
dmxdev = kernel / "drivers/media/dvb-core/dmxdev.c"
text = dmxdev.read_text()
anchor = "int dvb_dmxdev_init("
if text.count(anchor) != 1:
    raise SystemExit("drivers/media/dvb-core/dmxdev.c: dvb_dmxdev_init anchor is not unique")
start = text.index(anchor)
end = function_end(text, start)
fn = text[start:end]

for required in (
    "DMX_CAP_AUTO_BUFFER_FLUSH",
    "overflow_auto_flush",
    "get_caps",
):
    if required not in fn:
        raise SystemExit(
            "drivers/media/dvb-core/dmxdev.c: Samsung capability block is incomplete: "
            + required
        )

decl_re = re.compile(r"(?m)^\s*struct\s+dmx_caps\s+caps\s*;\s*$")
declarations = decl_re.findall(fn)
if len(declarations) == 0:
    brace = fn.find("{")
    if brace < 0:
        raise SystemExit("drivers/media/dvb-core/dmxdev.c: init opening brace missing")
    fn = fn[:brace + 1] + "\n\tstruct dmx_caps caps;" + fn[brace + 1:]
    text = text[:start] + fn + text[end:]
    dmxdev.write_text(text)
    state = "restored-samsung-local-declaration"
elif len(declarations) == 1:
    state = "already-present"
else:
    raise SystemExit(
        "drivers/media/dvb-core/dmxdev.c: duplicate struct dmx_caps caps declarations"
    )

post = dmxdev.read_text()
post_start = post.index(anchor)
post_end = function_end(post, post_start)
post_fn = post[post_start:post_end]
if len(decl_re.findall(post_fn)) != 1:
    raise SystemExit("drivers/media/dvb-core/dmxdev.c: caps declaration postcondition failed")
for required in (
    "DMX_CAP_AUTO_BUFFER_FLUSH",
    "overflow_auto_flush",
    "get_caps",
):
    if required not in post_fn:
        raise SystemExit(
            "drivers/media/dvb-core/dmxdev.c: capability behavior lost after repair: "
            + required
        )

with report.open("a") as fh:
    fh.write(f"dvb_dmxdev_caps={state}\n")
PY

python3 - "$KERNEL" "$REPORT" <<'PY'
from pathlib import Path
import sys

kernel = Path(sys.argv[1])
report = Path(sys.argv[2])
path = kernel / "drivers/iommu/io-pgtable-arm.c"
text = path.read_text()

# v4.19.236 carries ARM_LPAE_PTE_ADDR_MASK plus paddr_to_iopte() as part of
# ARM-LPAE 52-bit physical-address support. Samsung's tree independently
# modified the same PTE area for its table-refcount metadata, so the policy
# merge can retain Samsung's block while dropping both stable pieces. Phase100
# encountered the same shape and proved that restoring the exact stable mask
# before restoring paddr_to_iopte() is the correct reconciliation.
paddr_mask = "#define ARM_LPAE_PTE_ADDR_MASK\t\tGENMASK_ULL(47,12)"
mask_name = "#define ARM_LPAE_PTE_ADDR_MASK"
if paddr_mask not in text:
    if mask_name in text:
        raise SystemExit(
            "drivers/iommu/io-pgtable-arm.c: unexpected ARM_LPAE_PTE_ADDR_MASK spelling/value"
        )
    pte_page_anchor = "#define ARM_LPAE_PTE_TYPE_PAGE\t\t3\n"
    if text.count(pte_page_anchor) != 1:
        raise SystemExit(
            "drivers/iommu/io-pgtable-arm.c: PTE type-page anchor is not unique"
        )
    text = text.replace(
        pte_page_anchor,
        pte_page_anchor + "\n" + paddr_mask + "\n",
        1,
    )
    mask_state = "restored-stable-419236-mask"
elif text.count(paddr_mask) == 1:
    mask_state = "already-present"
else:
    raise SystemExit(
        "drivers/iommu/io-pgtable-arm.c: ARM_LPAE_PTE_ADDR_MASK is duplicated"
    )

# The merge retained stable's corrected table-descriptor call but dropped the
# helper definition. GCC accepts that as an implicit external symbol because
# warnings are disabled for this vendor build, then vmlinux fails at final link.
# Restore the exact v4.19.236 helper rather than falling back to raw __pa().
sig = "static arm_lpae_iopte paddr_to_iopte(phys_addr_t paddr,"
typedef_anchor = "typedef u64 arm_lpae_iopte;\n"
helper = '''static arm_lpae_iopte paddr_to_iopte(phys_addr_t paddr,
\t\t\t\t     struct arm_lpae_io_pgtable *data)
{
\tarm_lpae_iopte pte = paddr;

\t/* Of the bits which overlap, either 51:48 or 15:12 are always RES0 */
\treturn (pte | (pte >> (48 - 12))) & ARM_LPAE_PTE_ADDR_MASK;
}
'''

def_count = text.count(sig)
if def_count == 0:
    if text.count(typedef_anchor) != 1:
        raise SystemExit(
            "drivers/iommu/io-pgtable-arm.c: arm_lpae_iopte typedef anchor is not unique"
        )
    if text.count("paddr_to_iopte(__pa(table), data)") != 1:
        raise SystemExit(
            "drivers/iommu/io-pgtable-arm.c: stable table-descriptor caller is missing or duplicated"
        )
    if "iopte_tblcnt_set(&new, ref_count);" not in text:
        raise SystemExit(
            "drivers/iommu/io-pgtable-arm.c: Samsung table-refcount update is missing"
        )
    text = text.replace(typedef_anchor, typedef_anchor + "\n" + helper, 1)
    path.write_text(text)
    helper_state = "restored-stable-419236-definition"
elif def_count == 1:
    path.write_text(text)
    helper_state = "already-present"
else:
    raise SystemExit(
        "drivers/iommu/io-pgtable-arm.c: paddr_to_iopte definition is duplicated"
    )

post = path.read_text()
if post.count(paddr_mask) != 1:
    raise SystemExit(
        "drivers/iommu/io-pgtable-arm.c: ARM_LPAE_PTE_ADDR_MASK postcondition failed"
    )
if post.count(sig) != 1:
    raise SystemExit(
        "drivers/iommu/io-pgtable-arm.c: paddr_to_iopte definition postcondition failed"
    )
if post.count("paddr_to_iopte(__pa(table), data)") != 1:
    raise SystemExit(
        "drivers/iommu/io-pgtable-arm.c: stable table-descriptor encoding postcondition failed"
    )
if "new = __pa(table) | ARM_LPAE_PTE_TYPE_TABLE;" in post:
    raise SystemExit(
        "drivers/iommu/io-pgtable-arm.c: raw table-descriptor __pa encoding returned"
    )
for required in (
    "IOPTE_RESERVED_MASK",
    "iopte_val(",
    "iopte_tblcnt_set(&new, ref_count);",
):
    if required not in post:
        raise SystemExit(
            "drivers/iommu/io-pgtable-arm.c: Samsung IOPTE metadata behavior lost: "
            + required
        )

with report.open("a") as fh:
    fh.write(f"iommu_pte_addr_mask={mask_state}\n")
    fh.write(f"iommu_paddr_helper={helper_state}\n")
PY

python3 - "$KERNEL" "$REPORT" <<'PY'
from pathlib import Path
import sys

kernel = Path(sys.argv[1])
report = Path(sys.argv[2])
path = kernel / "arch/arm64/include/asm/sections.h"
text = path.read_text()

# The 4.19.236 BHB/vector backport adds entry_tramp_text_size() upstream.
# Samsung already carries the same helper in its vendor tree, so the merge can
# leave two byte-identical definitions. Phase100 hit and proved this exact
# reconciliation at 4.19.250: keep one canonical helper and reject any other
# shape rather than changing trampoline semantics.
entry_tramp_block = (
    "static inline size_t entry_tramp_text_size(void)\n"
    "{\n"
    "\treturn __entry_tramp_text_end - __entry_tramp_text_start;\n"
    "}\n"
)
count = text.count(entry_tramp_block)
if count == 2:
    first = text.find(entry_tramp_block)
    second = text.find(entry_tramp_block, first + len(entry_tramp_block))
    if first < 0 or second < 0:
        raise SystemExit(
            "arch/arm64/include/asm/sections.h: duplicate helper positions are invalid"
        )
    text = text[:second] + text[second + len(entry_tramp_block):]
    path.write_text(text)
    state = "deduped-phase100-proven-shape"
elif count == 1:
    state = "already-single"
else:
    raise SystemExit(
        "arch/arm64/include/asm/sections.h: "
        f"entry_tramp_text_size helper count is {count}, expected 1 or 2"
    )

post = path.read_text()
if post.count(entry_tramp_block) != 1:
    raise SystemExit(
        "arch/arm64/include/asm/sections.h: entry_tramp_text_size postcondition failed"
    )
if post.count("extern char __entry_tramp_text_start[], __entry_tramp_text_end[];") != 1:
    raise SystemExit(
        "arch/arm64/include/asm/sections.h: trampoline section symbols changed unexpectedly"
    )

with report.open("a") as fh:
    fh.write(f"arm64_entry_tramp_text_size={state}\n")
PY

git -C "$KERNEL" diff --check
cat "$REPORT"
echo "Phase102 Linux 4.19.236 compile-shape repair complete"
