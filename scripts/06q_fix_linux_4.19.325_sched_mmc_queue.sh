#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/sched-mmc-queue-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before scheduler/MMC queue repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
repairs = []

# Linux 4.19.325 classifier paths consume the shared RTM TCA policy exported
# by sch_api.c. The direct merge retained the consumers but dropped the header
# declaration while preserving Samsung's extra flow-control declaration.
pkt_sched = root / "include/net/pkt_sched.h"
pkt_text = pkt_sched.read_text()
policy_decl = "extern const struct nla_policy rtm_tca_policy[TCA_MAX + 1];\n"
if policy_decl not in pkt_text:
    anchor = (
        "extern int tc_qdisc_flow_control(struct net_device *dev, u32 tcm_handle,\n"
        "\t\t\t\t  int flow_enable);\n"
    )
    if pkt_text.count(anchor) != 1:
        raise SystemExit(
            f"pkt_sched flow-control anchor mismatch: {pkt_text.count(anchor)}"
        )
    pkt_text = pkt_text.replace(anchor, anchor + "\n" + policy_decl, 1)
    pkt_sched.write_text(pkt_text)
    repairs.append("pkt_sched=restored-rtm-tca-policy-declaration")
elif pkt_text.count(policy_decl) != 1:
    raise SystemExit("unexpected rtm_tca_policy declaration count")

# The merge combined upstream's data-sector-size validation with Samsung's
# host initialization and SD-card queue tuning, but lost the opening brace and
# nonzero guard. Restore only the malformed MMC-card block.
queue = root / "drivers/mmc/core/queue.c"
queue_text = queue.read_text()
old_block = (
    "\tif (mmc_card_mmc(card))\n"
    "\t\tblock_size = card->ext_csd.data_sector_size;\n"
    "\t\tWARN_ON(block_size != 512 && block_size != 4096);\n"
    "\t}\n"
)
new_block = (
    "\tif (mmc_card_mmc(card) && card->ext_csd.data_sector_size) {\n"
    "\t\tblock_size = card->ext_csd.data_sector_size;\n"
    "\t\tWARN_ON(block_size != 512 && block_size != 4096);\n"
    "\t}\n"
)
if old_block in queue_text:
    if queue_text.count(old_block) != 1:
        raise SystemExit("unexpected malformed MMC sector-size block count")
    queue_text = queue_text.replace(old_block, new_block, 1)
    queue.write_text(queue_text)
    repairs.append("mmc_queue=restored-sector-size-guard-and-braces")
elif queue_text.count(new_block) != 1:
    raise SystemExit("MMC sector-size block is neither malformed nor repaired")

# Exact postconditions.
final_pkt = pkt_sched.read_text()
if final_pkt.count(policy_decl) != 1:
    raise SystemExit("rtm_tca_policy declaration repair failed")

final_queue = queue.read_text()
if final_queue.count(new_block) != 1 or old_block in final_queue:
    raise SystemExit("MMC sector-size block repair failed")
for vendor_anchor in (
    "\tif (host->ops->init)\n\t\thost->ops->init(host);\n",
    "\tif (mmc_card_sd(card)) {\n",
    "cqe_crypto_update_queue(host, mq->queue);\n",
):
    if vendor_anchor not in final_queue:
        raise SystemExit(f"MMC vendor queue behavior disappeared: {vendor_anchor.strip()!r}")

print("\n".join(repairs or ["repairs=already-present"]))
PY

git -C "$KERNEL_DIR" diff --check -- \
  include/net/pkt_sched.h \
  drivers/mmc/core/queue.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'pkt_sched=restored-rtm-tca-policy-declaration\n'
  printf 'mmc_queue=restored-sector-size-guard-and-braces\n'
  printf 'result=linux-4.19.325-scheduler-mmc-queue-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION scheduler and MMC queue compatibility repaired"
