#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/tcp-dvb-fec-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before TCP/DVB/FEC repair"

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


# Samsung exposes tcp_enter_quickack_mode() to vendor users and declares it in
# include/net/tcp.h. The direct stable merge restored upstream's static keyword,
# creating a linkage conflict. Keep the vendor-visible symbol and ensure its
# export remains present exactly once.
tcp = root / "net/ipv4/tcp_input.c"
tcp_text = tcp.read_text()
static_sig = (
    "static void tcp_enter_quickack_mode(struct sock *sk, "
    "unsigned int max_quickacks)\n"
)
public_sig = (
    "void tcp_enter_quickack_mode(struct sock *sk, "
    "unsigned int max_quickacks)\n"
)
if static_sig in tcp_text:
    if tcp_text.count(static_sig) != 1:
        raise SystemExit("unexpected static tcp_enter_quickack_mode count")
    tcp_text = tcp_text.replace(static_sig, public_sig, 1)
elif tcp_text.count(public_sig) != 1:
    raise SystemExit("tcp_enter_quickack_mode definition is neither static nor public")

export = "EXPORT_SYMBOL(tcp_enter_quickack_mode);\n"
if export not in tcp_text:
    function = (
        public_sig
        + "{\n"
        + "\tstruct inet_connection_sock *icsk = inet_csk(sk);\n"
        + "\n"
        + "\ttcp_incr_quickack(sk, max_quickacks);\n"
        + "\ticsk->icsk_ack.pingpong = 0;\n"
        + "\ticsk->icsk_ack.ato = TCP_ATO_MIN;\n"
        + "}\n"
    )
    if tcp_text.count(function) != 1:
        raise SystemExit(
            f"TCP quickack function anchor mismatch: {tcp_text.count(function)}"
        )
    tcp_text = tcp_text.replace(function, function + export, 1)
elif tcp_text.count(export) != 1:
    raise SystemExit("unexpected tcp_enter_quickack_mode export count")
tcp.write_text(tcp_text)

# The merged DVB UAPI uses the later DMX_BUF_FLAG_* spelling. Update the one
# Samsung call site that retained the old DMX_BUFFER_FLAG_* spelling.
dvb = root / "drivers/media/dvb-core/dvb_demux.c"
old_flag = "DMX_BUFFER_FLAG_DISCONTINUITY_DETECTED"
new_flag = "DMX_BUF_FLAG_DISCONTINUITY_DETECTED"
dvb_text = dvb.read_text()
if old_flag in dvb_text:
    if dvb_text.count(old_flag) != 1:
        raise SystemExit(f"unexpected obsolete DVB flag count: {dvb_text.count(old_flag)}")
    dvb.write_text(dvb_text.replace(old_flag, new_flag, 1))
elif dvb_text.count(new_flag) < 1:
    raise SystemExit("DVB discontinuity flag is neither old nor repaired")

# Upstream 4.19.325 validates both hash-device and FEC-device sizes. The direct
# merge retained the calculation but dropped fec_blocks from the local u64 list.
fec = root / "drivers/md/dm-verity-fec.c"
fec_text = fec.read_text()
func_start = fec_text.index("int verity_fec_ctr(struct dm_verity *v)\n")
func_end = fec_text.index("\n}\n", func_start) + 3
segment = fec_text[func_start:func_end]
if "u64 hash_blocks, fec_blocks;\n" not in segment:
    old_decl = "\tu64 hash_blocks;\n"
    if segment.count(old_decl) != 1:
        raise SystemExit(
            f"verity FEC declaration anchor mismatch: {segment.count(old_decl)}"
        )
    segment = segment.replace(old_decl, "\tu64 hash_blocks, fec_blocks;\n", 1)
    fec_text = fec_text[:func_start] + segment + fec_text[func_end:]
    fec.write_text(fec_text)
elif segment.count("u64 hash_blocks, fec_blocks;\n") != 1:
    raise SystemExit("unexpected verity FEC declaration count")

# Exact postconditions.
final_tcp = tcp.read_text()
if final_tcp.count(public_sig) != 1 or static_sig in final_tcp:
    raise SystemExit("TCP quickack linkage repair failed")
if final_tcp.count(export) != 1:
    raise SystemExit("TCP quickack export repair failed")
final_dvb = dvb.read_text()
if old_flag in final_dvb or final_dvb.count(new_flag) < 1:
    raise SystemExit("DVB discontinuity flag repair failed")
final_fec = fec.read_text()
final_start = final_fec.index("int verity_fec_ctr(struct dm_verity *v)\n")
final_end = final_fec.index("\n}\n", final_start) + 3
if final_fec[final_start:final_end].count("u64 hash_blocks, fec_blocks;\n") != 1:
    raise SystemExit("verity FEC declaration repair failed")
PY

git -C "$KERNEL_DIR" diff --check -- \
  net/ipv4/tcp_input.c \
  drivers/media/dvb-core/dvb_demux.c \
  drivers/md/dm-verity-fec.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'tcp=restored-public-exported-quickack-helper\n'
  printf 'dvb=updated-discontinuity-buffer-flag-name\n'
  printf 'verity_fec=restored-fec-block-count-local\n'
  printf 'result=linux-4.19.325-tcp-dvb-fec-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION TCP, DVB and verity FEC compatibility repaired"
