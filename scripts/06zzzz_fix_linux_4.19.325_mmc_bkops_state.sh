#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/mmc-bkops-state-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before MMC BKOPS repair"

python3 - "$KERNEL_DIR/drivers/mmc/core/card.h" "$REPORT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = Path(sys.argv[2])
text = path.read_text()
repairs = []

old = '''#define MMC_STATE_PRESENT\t(1<<0)\t\t/* present in sysfs */
#define MMC_STATE_READONLY\t(1<<1)\t\t/* card is read-only */
#define MMC_STATE_BLOCKADDR\t(1<<2)\t\t/* card uses block-addressing */
#define MMC_CARD_SDXC\t\t(1<<3)\t\t/* card is SDXC */
#define MMC_CARD_REMOVED\t(1<<4)\t\t/* card has been removed */
#define MMC_STATE_SUSPENDED\t(1<<5)\t\t/* card is suspended */

#define mmc_card_present(c)\t((c)->state & MMC_STATE_PRESENT)
#define mmc_card_readonly(c)\t((c)->state & MMC_STATE_READONLY)
#define mmc_card_blockaddr(c)\t((c)->state & MMC_STATE_BLOCKADDR)
#define mmc_card_ext_capacity(c) ((c)->state & MMC_CARD_SDXC)
#define mmc_card_removed(c)\t((c) && ((c)->state & MMC_CARD_REMOVED))
#define mmc_card_suspended(c)\t((c)->state & MMC_STATE_SUSPENDED)

#define mmc_card_set_present(c)\t((c)->state |= MMC_STATE_PRESENT)
#define mmc_card_set_readonly(c) ((c)->state |= MMC_STATE_READONLY)
#define mmc_card_set_blockaddr(c) ((c)->state |= MMC_STATE_BLOCKADDR)
#define mmc_card_set_ext_capacity(c) ((c)->state |= MMC_CARD_SDXC)
#define mmc_card_set_removed(c) ((c)->state |= MMC_CARD_REMOVED)
#define mmc_card_set_suspended(c) ((c)->state |= MMC_STATE_SUSPENDED)
#define mmc_card_clr_suspended(c) ((c)->state &= ~MMC_STATE_SUSPENDED)
'''

new = '''#define MMC_STATE_PRESENT\t(1<<0)\t\t/* present in sysfs */
#define MMC_STATE_READONLY\t(1<<1)\t\t/* card is read-only */
#define MMC_STATE_BLOCKADDR\t(1<<2)\t\t/* card uses block-addressing */
#define MMC_CARD_SDXC\t\t(1<<3)\t\t/* card is SDXC */
#define MMC_CARD_REMOVED\t(1<<4)\t\t/* card has been removed */
#define MMC_STATE_DOING_BKOPS\t(1<<5)\t\t/* card is doing BKOPS */
#define MMC_STATE_SUSPENDED\t(1<<6)\t\t/* card is suspended */

#define mmc_card_present(c)\t((c)->state & MMC_STATE_PRESENT)
#define mmc_card_readonly(c)\t((c)->state & MMC_STATE_READONLY)
#define mmc_card_blockaddr(c)\t((c)->state & MMC_STATE_BLOCKADDR)
#define mmc_card_ext_capacity(c) ((c)->state & MMC_CARD_SDXC)
#define mmc_card_removed(c)\t((c) && ((c)->state & MMC_CARD_REMOVED))
#define mmc_card_doing_bkops(c)\t((c)->state & MMC_STATE_DOING_BKOPS)
#define mmc_card_suspended(c)\t((c)->state & MMC_STATE_SUSPENDED)

#define mmc_card_set_present(c)\t((c)->state |= MMC_STATE_PRESENT)
#define mmc_card_set_readonly(c) ((c)->state |= MMC_STATE_READONLY)
#define mmc_card_set_blockaddr(c) ((c)->state |= MMC_STATE_BLOCKADDR)
#define mmc_card_set_ext_capacity(c) ((c)->state |= MMC_CARD_SDXC)
#define mmc_card_set_removed(c) ((c)->state |= MMC_CARD_REMOVED)
#define mmc_card_set_doing_bkops(c)\t((c)->state |= MMC_STATE_DOING_BKOPS)
#define mmc_card_clr_doing_bkops(c)\t((c)->state &= ~MMC_STATE_DOING_BKOPS)
#define mmc_card_set_suspended(c) ((c)->state |= MMC_STATE_SUSPENDED)
#define mmc_card_clr_suspended(c) ((c)->state &= ~MMC_STATE_SUSPENDED)
'''

if old in text:
    if text.count(old) != 1:
        raise SystemExit(f"unexpected stable MMC state block count: {text.count(old)}")
    text = text.replace(old, new, 1)
    path.write_text(text)
    repairs.append("drivers/mmc/core/card.h=restored-vendor-bkops-state-layout")
elif text.count(new) != 1:
    raise SystemExit("MMC card state layout is neither stable nor repaired")

final = path.read_text()
required = (
    "#define MMC_STATE_DOING_BKOPS\t(1<<5)",
    "#define MMC_STATE_SUSPENDED\t(1<<6)",
    "#define mmc_card_doing_bkops(c)",
    "#define mmc_card_set_doing_bkops(c)",
    "#define mmc_card_clr_doing_bkops(c)",
)
for item in required:
    if final.count(item) != 1:
        raise SystemExit(f"MMC BKOPS postcondition failed: {item!r}")
if "#define MMC_STATE_SUSPENDED\t(1<<5)" in final:
    raise SystemExit("MMC suspended state still overlaps the BKOPS bit")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/mmc/core/card.h

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'mmc_bkops_state=bit5\n'
  printf 'mmc_suspended_state=bit6\n'
  printf 'result=linux-4.19.325-mmc-bkops-state-repaired\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION MMC BKOPS state helpers repaired"
