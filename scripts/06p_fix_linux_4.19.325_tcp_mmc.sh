#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/tcp-mmc-sdhci-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before TCP/MMC/SDHCI repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
repairs = []


# The direct merge can retain a transient two-argument form while the final
# 4.19.325 callers and function body use the one-argument API.
tcp = root / "include/net/tcp.h"
tcp_text = tcp.read_text()
old_tcp_sig = (
    "static inline void tcp_dec_quickack_mode(struct sock *sk,\n"
    "\t\t\t\t\t const unsigned int pkts)\n"
)
new_tcp_sig = "static inline void tcp_dec_quickack_mode(struct sock *sk)\n"
if old_tcp_sig in tcp_text:
    if tcp_text.count(old_tcp_sig) != 1:
        raise SystemExit("unexpected two-argument tcp_dec_quickack_mode count")
    tcp_text = tcp_text.replace(old_tcp_sig, new_tcp_sig, 1)
    tcp.write_text(tcp_text)
    repairs.append("tcp_dec_quickack_mode=restored-one-argument-api")
elif tcp_text.count(new_tcp_sig) != 1:
    raise SystemExit("tcp_dec_quickack_mode signature is neither old nor repaired")


# The stable helper mmc_cache_enabled() needs the matching bus-op member while
# Samsung's additional callbacks remain intact.
core = root / "drivers/mmc/core/core.h"
core_text = core.read_text()
struct_start = core_text.index("struct mmc_bus_ops {\n")
struct_end = core_text.index("};\n", struct_start) + 3
struct_segment = core_text[struct_start:struct_end]
cache_member = "\tbool (*cache_enabled)(struct mmc_host *);\n"
if cache_member not in struct_segment:
    anchor = "\tint (*sw_reset)(struct mmc_host *);\n"
    if struct_segment.count(anchor) != 1:
        raise SystemExit(
            f"mmc_bus_ops sw_reset anchor mismatch: {struct_segment.count(anchor)}"
        )
    struct_segment = struct_segment.replace(anchor, anchor + cache_member, 1)
    core_text = core_text[:struct_start] + struct_segment + core_text[struct_end:]
    core.write_text(core_text)
    repairs.append("mmc_bus_ops=restored-cache-enabled-callback")
elif struct_segment.count(cache_member) != 1:
    raise SystemExit("unexpected mmc_bus_ops cache_enabled member count")


# Preserve the voltage-switch optimization in both source layouts encountered
# in this tree. Samsung's older form holds host->lock and must unlock before the
# early return. The final stable form is lockless and must keep the plain return.
sdhci = root / "drivers/mmc/host/sdhci.c"
sdhci_text = sdhci.read_text()
func_start = sdhci_text.index(
    "void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n"
)
export_anchor = "\nEXPORT_SYMBOL_GPL(sdhci_set_ios);"
func_end = sdhci_text.index(export_anchor, func_start)
segment = sdhci_text[func_start:func_end]

reinit_decl = "\tbool reinit_uhs = host->reinit_uhs;\n"
turning_decl = "\tbool turning_on_clk = false;\n"
if reinit_decl not in segment or turning_decl not in segment:
    lock_decls = (
        "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
        "\tunsigned long flags;\n"
        "\tu8 ctrl;\n"
        "\tint ret;\n"
    )
    lock_new = (
        "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
        + reinit_decl + turning_decl +
        "\tunsigned long flags;\n"
        "\tu8 ctrl;\n"
        "\tint ret;\n"
    )
    stable_decls = (
        "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
        "\tu8 ctrl;\n"
    )
    stable_new = (
        "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
        + reinit_decl + turning_decl +
        "\tu8 ctrl;\n"
    )
    if segment.count(lock_decls) == 1:
        segment = segment.replace(lock_decls, lock_new, 1)
        repairs.append("sdhci=restored-state-in-locked-layout")
    elif segment.count(stable_decls) == 1:
        segment = segment.replace(stable_decls, stable_new, 1)
        repairs.append("sdhci=restored-state-in-stable-lockless-layout")
    else:
        raise SystemExit("sdhci_set_ios declaration layout is not recognized")

clock_assignment = "\t\tturning_on_clk = ios->clock && !host->clock;\n"
if clock_assignment not in segment:
    stable_clock = "\tif (!ios->clock || ios->clock != host->clock) {\n"
    vendor_clock = (
        "\tif (ios->clock &&\n"
        "\t    ((ios->clock != host->clock) || (ios->timing != host->timing))) {\n"
    )
    if segment.count(stable_clock) == 1:
        segment = segment.replace(stable_clock, stable_clock + clock_assignment, 1)
    elif segment.count(vendor_clock) == 1:
        segment = segment.replace(vendor_clock, vendor_clock + clock_assignment, 1)
    else:
        raise SystemExit("sdhci clock-change anchor is not recognized")
    repairs.append("sdhci=restored-turning-on-clock-tracking")

plain_fast_path = (
    "\tif (!reinit_uhs &&\n"
    "\t    turning_on_clk &&\n"
    "\t    host->timing == ios->timing &&\n"
    "\t    host->version >= SDHCI_SPEC_300 &&\n"
    "\t    !sdhci_presetable_values_change(host, ios))\n"
    "\t\treturn;\n"
)
unlocked_fast_path = (
    "\tif (!reinit_uhs &&\n"
    "\t    turning_on_clk &&\n"
    "\t    host->timing == ios->timing &&\n"
    "\t    host->version >= SDHCI_SPEC_300 &&\n"
    "\t    !sdhci_presetable_values_change(host, ios)) {\n"
    "\t\tspin_unlock_irqrestore(&host->lock, flags);\n"
    "\t\treturn;\n"
    "\t}\n"
)
lock_pos = segment.find("spin_lock_irqsave(&host->lock, flags);")
fast_pos = segment.find(plain_fast_path)
if lock_pos >= 0 and fast_pos > lock_pos:
    segment = segment.replace(plain_fast_path, unlocked_fast_path, 1)
    repairs.append("sdhci=release-lock-before-voltage-switch-fast-return")
elif plain_fast_path in segment:
    repairs.append("sdhci=stable-lockless-fast-return-preserved")
elif unlocked_fast_path in segment:
    repairs.append("sdhci=locked-fast-return-already-repaired")
else:
    raise SystemExit("SDHCI voltage-switch fast path is not recognized")

sdhci_text = sdhci_text[:func_start] + segment + sdhci_text[func_end:]
sdhci.write_text(sdhci_text)


# Exact postconditions.
final_tcp = tcp.read_text()
if final_tcp.count(new_tcp_sig) != 1 or old_tcp_sig in final_tcp:
    raise SystemExit("TCP quickack API repair failed")
final_core = core.read_text()
final_struct_start = final_core.index("struct mmc_bus_ops {\n")
final_struct_end = final_core.index("};\n", final_struct_start) + 3
if final_core[final_struct_start:final_struct_end].count(cache_member) != 1:
    raise SystemExit("MMC cache callback repair failed")
if "static inline bool mmc_cache_enabled(struct mmc_host *host)\n" not in final_core:
    raise SystemExit("MMC cache helper disappeared during repair")

final_sdhci = sdhci.read_text()
final_start = final_sdhci.index(
    "void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n"
)
final_end = final_sdhci.index(export_anchor, final_start)
final_segment = final_sdhci[final_start:final_end]
for required in (reinit_decl, turning_decl, clock_assignment):
    if final_segment.count(required) != 1:
        raise SystemExit(f"SDHCI postcondition failed for {required.strip()!r}")
if plain_fast_path not in final_segment and unlocked_fast_path not in final_segment:
    raise SystemExit("SDHCI fast-return postcondition failed")

print("\n".join(repairs or ["repairs=already-present"]))
PY

git -C "$KERNEL_DIR" diff --check -- \
  include/net/tcp.h \
  drivers/mmc/core/core.h \
  drivers/mmc/host/sdhci.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'tcp=restored-one-argument-quickack-api\n'
  printf 'mmc=restored-cache-enabled-bus-callback\n'
  printf 'sdhci=validated-locked-or-stable-lockless-set-ios\n'
  printf 'result=linux-4.19.325-tcp-mmc-sdhci-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION TCP, MMC and SDHCI compatibility repaired"
