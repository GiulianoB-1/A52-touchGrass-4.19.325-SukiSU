#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/tcp-mmc-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before TCP/MMC repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))


# Linux 4.19.325 changed this call to the newer one-argument quickack helper,
# while Samsung retains the older exported two-argument API. Preserve that API
# and reproduce the newer helper's packet-count calculation at this call site.
tcp_output = root / "net/ipv4/tcp_output.c"
replace_once(
    tcp_output,
    "\ttcp_dec_quickack_mode(sk);\n",
    "\ttcp_dec_quickack_mode(sk,\n"
    "\t\t\t      inet_csk_ack_scheduled(sk) ? 1 : 0);\n",
    "TCP quickack decrement call",
)

# The stable MMC cache query helper was merged, but its bus callback member was
# lost while retaining Samsung's deferred-resume and bus-speed callbacks.
core_h = root / "drivers/mmc/core/core.h"
core_text = core_h.read_text()
if "bool (*cache_enabled)(struct mmc_host *);" not in core_text:
    anchor = "\tint (*sw_reset)(struct mmc_host *);\n"
    replacement = anchor + "\tbool (*cache_enabled)(struct mmc_host *);\n"
    replace_once(core_h, anchor, replacement, "MMC cache-enabled callback")
elif core_text.count("bool (*cache_enabled)(struct mmc_host *);") != 1:
    raise SystemExit("unexpected MMC cache_enabled callback count")

# The direct merge inserted the 4.19.325 preset-value early-exit into Samsung's
# lock-based sdhci_set_ios(), but dropped the state variables and their clock
# transition assignment. It also left a bare return while host->lock is held.
sdhci = root / "drivers/mmc/host/sdhci.c"
sdhci_text = sdhci.read_text()
func_start = sdhci_text.index(
    "void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n"
)
func_end = sdhci_text.index("EXPORT_SYMBOL_GPL(sdhci_set_ios);", func_start)
segment = sdhci_text[func_start:func_end]

old_decls = (
    "{\n"
    "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
    "\tunsigned long flags;\n"
    "\tu8 ctrl;\n"
    "\tint ret;\n"
    "\n"
    "\thost->reinit_uhs = false;\n"
)
new_decls = (
    "{\n"
    "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
    "\tbool reinit_uhs = host->reinit_uhs;\n"
    "\tbool turning_on_clk = false;\n"
    "\tunsigned long flags;\n"
    "\tu8 ctrl;\n"
    "\tint ret;\n"
    "\n"
    "\thost->reinit_uhs = false;\n"
)
if "bool reinit_uhs = host->reinit_uhs;" not in segment:
    if segment.count(old_decls) != 1:
        raise SystemExit(
            f"SDHCI declaration anchor mismatch: {segment.count(old_decls)}"
        )
    segment = segment.replace(old_decls, new_decls, 1)

clock_anchor = (
    "\tif (ios->clock &&\n"
    "\t    ((ios->clock != host->clock) || (ios->timing != host->timing))) {\n"
    "\t\tspin_unlock_irqrestore(&host->lock, flags);\n"
)
clock_replacement = (
    "\tif (ios->clock &&\n"
    "\t    ((ios->clock != host->clock) || (ios->timing != host->timing))) {\n"
    "\t\tturning_on_clk = !host->clock;\n"
    "\t\tspin_unlock_irqrestore(&host->lock, flags);\n"
)
if "\t\tturning_on_clk = !host->clock;\n" not in segment:
    if segment.count(clock_anchor) != 1:
        raise SystemExit(
            f"SDHCI clock-transition anchor mismatch: {segment.count(clock_anchor)}"
        )
    segment = segment.replace(clock_anchor, clock_replacement, 1)

early_return = (
    "\tif (!reinit_uhs &&\n"
    "\t    turning_on_clk &&\n"
    "\t    host->timing == ios->timing &&\n"
    "\t    host->version >= SDHCI_SPEC_300 &&\n"
    "\t    !sdhci_presetable_values_change(host, ios))\n"
    "\t\treturn;\n"
)
safe_early_return = (
    "\tif (!reinit_uhs &&\n"
    "\t    turning_on_clk &&\n"
    "\t    host->timing == ios->timing &&\n"
    "\t    host->version >= SDHCI_SPEC_300 &&\n"
    "\t    !sdhci_presetable_values_change(host, ios)) {\n"
    "\t\tspin_unlock_irqrestore(&host->lock, flags);\n"
    "\t\treturn;\n"
    "\t}\n"
)
if safe_early_return not in segment:
    if segment.count(early_return) != 1:
        raise SystemExit(
            f"SDHCI preset early-return anchor mismatch: {segment.count(early_return)}"
        )
    segment = segment.replace(early_return, safe_early_return, 1)

sdhci_text = sdhci_text[:func_start] + segment + sdhci_text[func_end:]
sdhci.write_text(sdhci_text)

# Exact postconditions.
final_tcp = tcp_output.read_text()
if "tcp_dec_quickack_mode(sk);" in final_tcp:
    raise SystemExit("obsolete one-argument TCP quickack call remains")
if final_tcp.count("inet_csk_ack_scheduled(sk) ? 1 : 0") != 1:
    raise SystemExit("TCP quickack packet-count adaptation failed")
final_core = core_h.read_text()
if final_core.count("bool (*cache_enabled)(struct mmc_host *);") != 1:
    raise SystemExit("MMC cache callback repair failed")
final_sdhci = sdhci.read_text()
final_start = final_sdhci.index(
    "void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n"
)
final_end = final_sdhci.index("EXPORT_SYMBOL_GPL(sdhci_set_ios);", final_start)
final_segment = final_sdhci[final_start:final_end]
for required in (
    "bool reinit_uhs = host->reinit_uhs;",
    "bool turning_on_clk = false;",
    "turning_on_clk = !host->clock;",
    "spin_unlock_irqrestore(&host->lock, flags);\n\t\treturn;",
):
    if required not in final_segment:
        raise SystemExit(f"SDHCI repair missing postcondition: {required}")
PY

git -C "$KERNEL_DIR" diff --check -- \
  net/ipv4/tcp_output.c \
  drivers/mmc/core/core.h \
  drivers/mmc/host/sdhci.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'tcp=adapted-new-ack-accounting-to-vendor-two-argument-helper\n'
  printf 'mmc_core=restored-cache-enabled-bus-callback\n'
  printf 'sdhci=restored-reinit-clock-state-and-safe-locked-early-return\n'
  printf 'result=linux-4.19.325-tcp-mmc-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION TCP and MMC compatibility repaired"
