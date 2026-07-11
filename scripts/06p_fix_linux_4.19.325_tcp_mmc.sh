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


# The direct merge retained a transient two-argument form of this helper while
# the 4.19.325 callers and function body use the final one-argument API.
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


# The stable helper mmc_cache_enabled() was merged, but its matching bus-op
# member was lost while preserving Samsung's additional vendor callbacks.
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


# The direct merge inserted the upstream voltage-switch fast path into Samsung's
# lock-based sdhci_set_ios(), but omitted its state variables and returned while
# host->lock was held. Restore the variables and release the lock on that path.
sdhci = root / "drivers/mmc/host/sdhci.c"
sdhci_text = sdhci.read_text()
func_start = sdhci_text.index("void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n")
export_anchor = "\nEXPORT_SYMBOL_GPL(sdhci_set_ios);"
func_end = sdhci_text.index(export_anchor, func_start)
segment = sdhci_text[func_start:func_end]

old_decls = (
    "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
    "\tunsigned long flags;\n"
    "\tu8 ctrl;\n"
    "\tint ret;\n"
    "\n"
    "\thost->reinit_uhs = false;\n"
)
new_decls = (
    "\tstruct sdhci_host *host = mmc_priv(mmc);\n"
    "\tbool reinit_uhs = host->reinit_uhs;\n"
    "\tbool turning_on_clk = false;\n"
    "\tunsigned long flags;\n"
    "\tu8 ctrl;\n"
    "\tint ret;\n"
    "\n"
    "\thost->reinit_uhs = false;\n"
)
if new_decls not in segment:
    if segment.count(old_decls) != 1:
        raise SystemExit(
            f"sdhci_set_ios declaration anchor mismatch: {segment.count(old_decls)}"
        )
    segment = segment.replace(old_decls, new_decls, 1)
    repairs.append("sdhci=restored-reinit-and-clock-state")
elif segment.count(new_decls) != 1:
    raise SystemExit("unexpected repaired sdhci_set_ios declaration count")

clock_anchor = (
    "\tif (ios->clock &&\n"
    "\t    ((ios->clock != host->clock) || (ios->timing != host->timing))) {\n"
)
clock_assignment = "\t\tturning_on_clk = ios->clock && !host->clock;\n"
if clock_assignment not in segment:
    if segment.count(clock_anchor) != 1:
        raise SystemExit(
            f"sdhci clock-change anchor mismatch: {segment.count(clock_anchor)}"
        )
    segment = segment.replace(clock_anchor, clock_anchor + clock_assignment, 1)
    repairs.append("sdhci=restored-turning-on-clock-tracking")
elif segment.count(clock_assignment) != 1:
    raise SystemExit("unexpected turning_on_clk assignment count")

old_fast_path = (
    "\tif (!reinit_uhs &&\n"
    "\t    turning_on_clk &&\n"
    "\t    host->timing == ios->timing &&\n"
    "\t    host->version >= SDHCI_SPEC_300 &&\n"
    "\t    !sdhci_presetable_values_change(host, ios))\n"
    "\t\treturn;\n"
)
new_fast_path = (
    "\tif (!reinit_uhs &&\n"
    "\t    turning_on_clk &&\n"
    "\t    host->timing == ios->timing &&\n"
    "\t    host->version >= SDHCI_SPEC_300 &&\n"
    "\t    !sdhci_presetable_values_change(host, ios)) {\n"
    "\t\tspin_unlock_irqrestore(&host->lock, flags);\n"
    "\t\treturn;\n"
    "\t}\n"
)
if old_fast_path in segment:
    if segment.count(old_fast_path) != 1:
        raise SystemExit("unexpected unsafe SDHCI fast-path count")
    segment = segment.replace(old_fast_path, new_fast_path, 1)
    repairs.append("sdhci=release-lock-before-voltage-switch-fast-return")
elif segment.count(new_fast_path) != 1:
    raise SystemExit("SDHCI voltage-switch fast path is neither old nor repaired")

sdhci_text = sdhci_text[:func_start] + segment + sdhci_text[func_end:]
sdhci.write_text(sdhci_text)


# The stable inet_sendmsg() path snapshots sk_prot because IPV6_ADDRFORM may
# change it concurrently. Preserve Samsung's analytics hook while consistently
# dispatching through that snapshot.
af_inet = root / "net/ipv4/af_inet.c"
af_text = af_inet.read_text()
af_start = af_text.index("int inet_sendmsg(struct socket *sock, struct msghdr *msg, size_t size)\n")
af_end = af_text.index("\nEXPORT_SYMBOL(inet_sendmsg);", af_start)
af_segment = af_text[af_start:af_end]
old_af_decls = (
    "{\n"
    "\tstruct sock *sk = sock->sk;\n"
    "#ifdef CONFIG_NET_ANALYTICS\n"
)
new_af_decls = (
    "{\n"
    "\tstruct sock *sk = sock->sk;\n"
    "\tconst struct proto *prot;\n"
    "#ifdef CONFIG_NET_ANALYTICS\n"
)
if "\tconst struct proto *prot;\n" not in af_segment:
    if af_segment.count(old_af_decls) != 1:
        raise SystemExit(
            f"inet_sendmsg declaration anchor mismatch: {af_segment.count(old_af_decls)}"
        )
    af_segment = af_segment.replace(old_af_decls, new_af_decls, 1)
    repairs.append("inet_sendmsg=restored-protocol-snapshot-declaration")
elif af_segment.count("\tconst struct proto *prot;\n") != 1:
    raise SystemExit("unexpected inet_sendmsg protocol declaration count")

old_analytics_send = "\terr = sk->sk_prot->sendmsg(sk, msg, size);\n"
new_analytics_send = "\terr = prot->sendmsg(sk, msg, size);\n"
if old_analytics_send in af_segment:
    if af_segment.count(old_analytics_send) != 1:
        raise SystemExit("unexpected analytics sendmsg dispatch count")
    af_segment = af_segment.replace(old_analytics_send, new_analytics_send, 1)
    repairs.append("inet_sendmsg=route-analytics-through-protocol-snapshot")
elif af_segment.count(new_analytics_send) != 1:
    raise SystemExit("inet_sendmsg analytics dispatch is neither old nor repaired")

old_plain_send = "\treturn sk->sk_prot->sendmsg(sk, msg, size);\n"
new_plain_send = "\treturn prot->sendmsg(sk, msg, size);\n"
if old_plain_send in af_segment:
    if af_segment.count(old_plain_send) != 1:
        raise SystemExit("unexpected plain sendmsg dispatch count")
    af_segment = af_segment.replace(old_plain_send, new_plain_send, 1)
    repairs.append("inet_sendmsg=route-default-through-protocol-snapshot")
elif af_segment.count(new_plain_send) != 1:
    raise SystemExit("inet_sendmsg default dispatch is neither old nor repaired")

af_text = af_text[:af_start] + af_segment + af_text[af_end:]
af_inet.write_text(af_text)


# The merged helper adopted the upstream queue and issue-type parameters but
# retained Samsung body lines that redeclared q and referenced the removed req.
# Keep Samsung's active-request accounting while using the new parameters.
block = root / "drivers/mmc/core/block.c"
block_text = block.read_text()
block_start = block_text.index(
    "static void mmc_blk_mq_dec_in_flight(struct mmc_queue *mq,\n"
)
block_end = block_text.index(
    "\nstatic void mmc_blk_mq_post_req(struct mmc_queue *mq, struct request *req)\n",
    block_start,
)
block_segment = block_text[block_start:block_end]
old_q_redecl = "\tstruct request_queue *q = req->q;\n"
if old_q_redecl in block_segment:
    if block_segment.count(old_q_redecl) != 1:
        raise SystemExit("unexpected MMC request-queue redeclaration count")
    block_segment = block_segment.replace(old_q_redecl, "", 1)
    repairs.append("mmc_block=remove-redeclared-queue-from-new-api")

old_issue_decrement = "\tmq->in_flight[mmc_issue_type(mq, req)] -= 1;\n"
new_issue_decrement = "\tmq->in_flight[issue_type] -= 1;\n"
if old_issue_decrement in block_segment:
    if block_segment.count(old_issue_decrement) != 1:
        raise SystemExit("unexpected MMC request-based issue decrement count")
    block_segment = block_segment.replace(old_issue_decrement, new_issue_decrement, 1)
    repairs.append("mmc_block=use-passed-issue-type")
elif block_segment.count(new_issue_decrement) != 1:
    raise SystemExit("MMC issue decrement is neither old nor repaired")

block_text = block_text[:block_start] + block_segment + block_text[block_end:]
block.write_text(block_text)


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

final_af = af_inet.read_text()
final_af_start = final_af.index("int inet_sendmsg(struct socket *sock, struct msghdr *msg, size_t size)\n")
final_af_end = final_af.index("\nEXPORT_SYMBOL(inet_sendmsg);", final_af_start)
final_af_segment = final_af[final_af_start:final_af_end]
for required in (
    "\tconst struct proto *prot;\n",
    "\tprot = READ_ONCE(sk->sk_prot);\n",
    new_analytics_send,
    new_plain_send,
):
    if final_af_segment.count(required) != 1:
        raise SystemExit(f"inet_sendmsg postcondition failed for {required.strip()!r}")
if "sk->sk_prot->sendmsg" in final_af_segment:
    raise SystemExit("inet_sendmsg still dispatches through a mutable sk_prot")

final_block = block.read_text()
final_block_start = final_block.index(
    "static void mmc_blk_mq_dec_in_flight(struct mmc_queue *mq,\n"
)
final_block_end = final_block.index(
    "\nstatic void mmc_blk_mq_post_req(struct mmc_queue *mq, struct request *req)\n",
    final_block_start,
)
final_block_segment = final_block[final_block_start:final_block_end]
for required in (
    "\tstruct mmc_host *host = mq->card->host;\n",
    new_issue_decrement,
    "\tatomic_dec(&host->active_reqs);\n",
):
    if final_block_segment.count(required) != 1:
        raise SystemExit(f"MMC block postcondition failed for {required.strip()!r}")
if old_q_redecl in final_block_segment or "mmc_issue_type(mq, req)" in final_block_segment:
    raise SystemExit("MMC block helper still references removed request parameter")

final_sdhci = sdhci.read_text()
final_start = final_sdhci.index("void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n")
final_end = final_sdhci.index(export_anchor, final_start)
final_segment = final_sdhci[final_start:final_end]
for required in (
    "\tbool reinit_uhs = host->reinit_uhs;\n",
    "\tbool turning_on_clk = false;\n",
    clock_assignment,
    new_fast_path,
):
    if final_segment.count(required) != 1:
        raise SystemExit(f"SDHCI postcondition failed for {required.strip()!r}")
if old_fast_path in final_segment:
    raise SystemExit("unsafe SDHCI locked return remains")

print("\n".join(repairs or ["repairs=already-present"]))
PY

git -C "$KERNEL_DIR" diff --check -- \
  include/net/tcp.h \
  net/ipv4/af_inet.c \
  drivers/mmc/core/core.h \
  drivers/mmc/core/block.c \
  drivers/mmc/host/sdhci.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'tcp=restored-one-argument-quickack-api\n'
  printf 'mmc=restored-cache-enabled-bus-callback\n'
  printf 'sdhci=restored-state-and-unlocked-fast-return\n'
  printf 'inet=restored-stable-protocol-snapshot-dispatch\n'
  printf 'mmc_block=merged-new-issue-api-with-vendor-accounting\n'
  printf 'result=linux-4.19.325-tcp-mmc-sdhci-inet-block-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION TCP, MMC, SDHCI and inet compatibility repaired"
