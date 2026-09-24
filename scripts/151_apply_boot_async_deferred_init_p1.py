#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
defconfig = kernel / "arch/arm64/configs/a52xq_defconfig"
nfc = kernel / "drivers/nfc/sec_nfc.c"
fp = kernel / "drivers/fingerprint/et7xx-spi.c"

for path in (defconfig, nfc, fp):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))

# P151A: defer the bulk of struct page initialization out of the
# single-threaded early boot path. The A52 defconfig already satisfies
# CONFIG_SPARSEMEM=y and CONFIG_NO_BOOTMEM=y.
replace_once(
    defconfig,
    "# CONFIG_DEFERRED_STRUCT_PAGE_INIT is not set\n",
    "CONFIG_DEFERRED_STRUCT_PAGE_INIT=y\n",
    "deferred struct page init",
)

# P151B: only mark non-boot-critical peripheral drivers asynchronous.
# Keep storage, display, PMIC, clocks, regulators, interconnect, IOMMU,
# CPUfreq/devfreq and eSE synchronous in this first phase.
replace_once(
    nfc,
    "\t\t.name = SEC_NFC_DRIVER_NAME,\n",
    "\t\t.name = SEC_NFC_DRIVER_NAME,\n"
    "\t\t.probe_type = PROBE_PREFER_ASYNCHRONOUS, /* A52 P151 */\n",
    "Samsung NFC async probe",
)

replace_once(
    fp,
    "\t\t.of_match_table = et7xx_match_table\n",
    "\t\t.of_match_table = et7xx_match_table,\n"
    "\t\t.probe_type = PROBE_PREFER_ASYNCHRONOUS, /* A52 P151 */\n",
    "ET7xx fingerprint async probe",
)

# Source-level audit.
cfg = defconfig.read_text()
nfc_text = nfc.read_text()
fp_text = fp.read_text()

assert "CONFIG_DEFERRED_STRUCT_PAGE_INIT=y" in cfg
assert "# CONFIG_DEFERRED_STRUCT_PAGE_INIT is not set" not in cfg
assert nfc_text.count("PROBE_PREFER_ASYNCHRONOUS, /* A52 P151 */") == 1
assert fp_text.count("PROBE_PREFER_ASYNCHRONOUS, /* A52 P151 */") == 1

print("A52 P151: selective async probe + deferred struct page init applied")
print("  CONFIG_DEFERRED_STRUCT_PAGE_INIT=y")
print("  async: Samsung sec_nfc")
print("  async: ET7xx fingerprint")
print("  synchronous by design: UFS/display/PMIC/clocks/regulators/interconnect/IOMMU/CPUfreq/devfreq/eSE")
