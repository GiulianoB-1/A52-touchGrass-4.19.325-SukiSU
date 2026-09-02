#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/inet-mmc-block-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before INET/MMC block repair"

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


# Linux 4.19.325 snapshots sk_prot because IPV6_ADDRFORM may change it under
# inet_sendmsg(). The direct merge retained READ_ONCE() but lost the local
# declaration, and Samsung's analytics branches still dereferenced sk_prot a
# second time. Use the stable snapshot for both vendor and non-vendor paths.
af_inet = root / "net/ipv4/af_inet.c"
text = af_inet.read_text()
func_start = text.index(
    "int inet_sendmsg(struct socket *sock, struct msghdr *msg, size_t size)\n"
)
func_end = text.index("EXPORT_SYMBOL(inet_sendmsg);", func_start)
segment = text[func_start:func_end]

if "\tconst struct proto *prot;\n" not in segment:
    anchor = "\tstruct sock *sk = sock->sk;\n"
    if segment.count(anchor) != 1:
        raise SystemExit(
            f"inet_sendmsg declaration anchor mismatch: {segment.count(anchor)}"
        )
    segment = segment.replace(anchor, anchor + "\tconst struct proto *prot;\n", 1)

old_send = "sk->sk_prot->sendmsg(sk, msg, size)"
new_send = "prot->sendmsg(sk, msg, size)"
old_count = segment.count(old_send)
if old_count:
    if old_count != 2:
        raise SystemExit(f"unexpected inet_sendmsg live sk_prot call count: {old_count}")
    segment = segment.replace(old_send, new_send)
elif segment.count(new_send) != 2:
    raise SystemExit("inet_sendmsg send calls are neither old nor repaired")

text = text[:func_start] + segment + text[func_end:]
af_inet.write_text(text)

# Stable 4.19.325 changed the in-flight helper to accept the queue and already
# computed issue type. Keep that race-safe interface while retaining Samsung's
# active_reqs accounting, which still needs the host pointer.
block = root / "drivers/mmc/core/block.c"
text = block.read_text()
old_body = (
    "static void mmc_blk_mq_dec_in_flight(struct mmc_queue *mq,\n"
    "\t\t\t\t     struct request_queue *q,\n"
    "\t\t\t\t     enum mmc_issue_type issue_type)\n"
    "{\n"
    "\tstruct request_queue *q = req->q;\n"
    "\tstruct mmc_host *host = mq->card->host;\n"
    "\tunsigned long flags;\n"
    "\tbool put_card;\n"
    "\n"
    "\tspin_lock_irqsave(q->queue_lock, flags);\n"
    "\n"
    "\tmq->in_flight[mmc_issue_type(mq, req)] -= 1;\n"
    "\tatomic_dec(&host->active_reqs);\n"
    "\n"
    "\tput_card = (mmc_tot_in_flight(mq) == 0);\n"
    "\n"
    "\tspin_unlock_irqrestore(q->queue_lock, flags);\n"
    "\n"
    "\tif (put_card)\n"
    "\t\tmmc_put_card(mq->card, &mq->ctx);\n"
    "}\n"
)
new_body = (
    "static void mmc_blk_mq_dec_in_flight(struct mmc_queue *mq,\n"
    "\t\t\t\t     struct request_queue *q,\n"
    "\t\t\t\t     enum mmc_issue_type issue_type)\n"
    "{\n"
    "\tstruct mmc_host *host = mq->card->host;\n"
    "\tunsigned long flags;\n"
    "\tbool put_card;\n"
    "\n"
    "\tspin_lock_irqsave(q->queue_lock, flags);\n"
    "\n"
    "\tmq->in_flight[issue_type] -= 1;\n"
    "\tatomic_dec(&host->active_reqs);\n"
    "\n"
    "\tput_card = (mmc_tot_in_flight(mq) == 0);\n"
    "\n"
    "\tspin_unlock_irqrestore(q->queue_lock, flags);\n"
    "\n"
    "\tif (put_card)\n"
    "\t\tmmc_put_card(mq->card, &mq->ctx);\n"
    "}\n"
)
if old_body in text:
    if text.count(old_body) != 1:
        raise SystemExit("unexpected malformed MMC in-flight helper count")
    text = text.replace(old_body, new_body, 1)
elif text.count(new_body) != 1:
    raise SystemExit("MMC in-flight helper is neither malformed nor repaired")
block.write_text(text)

# Exact postconditions.
final_inet = af_inet.read_text()
start = final_inet.index(
    "int inet_sendmsg(struct socket *sock, struct msghdr *msg, size_t size)\n"
)
end = final_inet.index("EXPORT_SYMBOL(inet_sendmsg);", start)
inet_segment = final_inet[start:end]
if inet_segment.count("const struct proto *prot;") != 1:
    raise SystemExit("inet_sendmsg protocol declaration repair failed")
if "sk->sk_prot->sendmsg" in inet_segment or inet_segment.count(new_send) != 2:
    raise SystemExit("inet_sendmsg protocol snapshot use repair failed")
final_block = block.read_text()
if final_block.count(new_body) != 1:
    raise SystemExit("MMC in-flight helper repair failed")
PY

git -C "$KERNEL_DIR" diff --check -- \
  net/ipv4/af_inet.c \
  drivers/mmc/core/block.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'inet=restored-stable-protocol-snapshot-through-vendor-send-paths\n'
  printf 'mmc_block=merged-stable-issue-type-interface-with-vendor-active-accounting\n'
  printf 'result=linux-4.19.325-inet-mmc-block-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION INET and MMC block compatibility repaired"
