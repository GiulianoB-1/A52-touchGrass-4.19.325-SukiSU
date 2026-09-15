#!/usr/bin/env bash
set -Eeuo pipefail

KERNEL="${1:-workspace/touchgrass-a52xq}"
KERNEL="$(cd "$KERNEL" && pwd)"
PATCH_URL="https://raw.githubusercontent.com/kucingoranye/kernel_patches/b249b8278b9691b73fc806ed379bd0c20c823b69/tcp/bbr-v3-kernel-4.19.patch"
PATCH_REF="b249b8278b9691b73fc806ed379bd0c20c823b69"
GOOGLE_BBR_COMMIT="cb31f3d02b1d7cd7cfdff4dd2b8b9d38879904af"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

test -f "$KERNEL/net/ipv4/tcp_bbr.c"
test -f "$KERNEL/net/ipv4/Kconfig"
test -f "$KERNEL/net/ipv4/Makefile"
test -f "$KERNEL/arch/arm64/configs/a52xq_defconfig"

echo "==> Saving Samsung/Linux-4.19 BBRv1"
cp "$KERNEL/net/ipv4/tcp_bbr.c" "$TMP/tcp_bbr_v1.c"

echo "==> Fetching pinned Linux-4.19 BBRv3 compatibility backport"
curl -fL --retry 3 "$PATCH_URL" -o "$TMP/bbr3-4.19.patch"
grep -Fq "$GOOGLE_BBR_COMMIT" "$TMP/bbr3-4.19.patch"
grep -Fq "backport bbr v3" "$TMP/bbr3-4.19.patch"

echo "==> Applying BBRv3 TCP-core compatibility plumbing"
cd "$KERNEL"
set +e
git apply --reject --whitespace=nowarn "$TMP/bbr3-4.19.patch"
rc=$?
set -e

# Resolve the small set of Samsung/touchGrass 4.19 semantic conflicts
# that the generic 4.19 BBRv3 compatibility patch cannot match by context.
python3 - "$KERNEL" <<'PY'
from pathlib import Path
import re, sys

root = Path(sys.argv[1])

def replace_once(path, old, new, label):
    p = root / path
    text = p.read_text()
    if old in text:
        p.write_text(text.replace(old, new, 1))
        return
    if new in text:
        return
    raise SystemExit(f"{label}: expected source anchor not found in {path}")

replace_once(
    "net/core/sock.c",
    "sk->sk_max_pacing_rate = ~0U;\n\tsk->sk_pacing_rate = ~0U;\n\tsk->sk_pacing_shift = 10;",
    "sk->sk_max_pacing_rate = ~0UL;\n\tsk->sk_pacing_rate = ~0UL;\n\tWRITE_ONCE(sk->sk_pacing_shift, 10);",
    "sock pacing width",
)

replace_once(
    "net/ipv4/tcp.c",
    "tp->reord_seen = 0;\n",
    "tp->reord_seen = 0;\n\ttp->fast_ack_mode = 0;\n",
    "tcp disconnect fast_ack reset",
)

replace_once(
    "net/ipv4/tcp_input.c",
    "tcp_process_tlp_ack(sk, ack, flag);",
    "tcp_process_tlp_ack(sk, ack, flag, &rs);",
    "TLP ACK rate-sample plumbing",
)

replace_once(
    "net/ipv4/tcp_output.c",
    "u64 len_ns;\n\tu32 rate;\n",
    "u64 len_ns;\n\tunsigned long rate;\n",
    "internal pacing rate width",
)
replace_once(
    "net/ipv4/tcp_output.c",
    "if (!rate || rate == ~0U)",
    "if (!rate || rate == ~0UL)",
    "internal pacing unlimited sentinel",
)

p = root / "net/ipv4/tcp_output.c"
text = p.read_text()
new = """static u32 tcp_tso_segs(struct sock *sk, unsigned int mss_now)
{
\tconst struct tcp_congestion_ops *ca_ops = inet_csk(sk)->icsk_ca_ops;
\tu32 tso_segs;

\ttso_segs = ca_ops->tso_segs ?
\t\t\tca_ops->tso_segs(sk, mss_now) :
\t\t\ttcp_tso_autosize(sk, mss_now,
\t\t\t\t sock_net(sk)->ipv4.sysctl_tcp_min_tso_segs);
\treturn min_t(u32, tso_segs, sk->sk_gso_max_segs);
}"""
if "ca_ops->tso_segs(sk, mss_now)" not in text:
    pat = re.compile(
        r"static u32 tcp_tso_segs\(struct sock \*sk, unsigned int mss_now\)\n"
        r"\{.*?\n\}",
        re.S,
    )
    m = pat.search(text)
    if not m:
        raise SystemExit("tcp_tso_segs: function not found")
    body = m.group(0)
    if "tcp_tso_autosize" not in body or "icsk_ca_ops" not in body:
        raise SystemExit("tcp_tso_segs: unexpected function shape")
    text = text[:m.start()] + new + text[m.end():]
    p.write_text(text)

# The generic patch can create another 4-argument TLP call elsewhere in the
# file, so verify the Samsung call site itself is no longer left behind.
tcp_input = (root / "net/ipv4/tcp_input.c").read_text()
if "tcp_process_tlp_ack(sk, ack, flag);" in tcp_input:
    raise SystemExit("TLP ACK rate-sample plumbing: stale 3-argument call remains")

for rel in (
    "net/core/sock.c.rej",
    "net/ipv4/tcp.c.rej",
    "net/ipv4/tcp_input.c.rej",
    "net/ipv4/tcp_output.c.rej",
):
    q = root / rel
    if q.exists():
        q.unlink()
PY

if find . -name '*.rej' -print -quit | grep -q .; then
    echo "ERROR: BBRv3 compatibility patch produced rejects:" >&2
    find . -name '*.rej' -print >&2
    while IFS= read -r rej; do
        echo "===== $rej =====" >&2
        cat "$rej" >&2
    done < <(find . -name '*.rej' -print)
    exit 2
fi
if [ "$rc" -ne 0 ]; then
    echo "BBRv3 compatibility patch returned rc=$rc because Samsung-specific hunks required manual resolution."
    echo "All rejects were resolved and removed; continuing after semantic validation."
fi

echo "==> Completing BBRv3 delivery-rate metadata backport"
python3 - "$KERNEL/net/ipv4/tcp_rate.c" <<'PY'
from pathlib import Path
import re, sys

p = Path(sys.argv[1])
s = p.read_text()

# The third-party 4.19 BBRv3 patch updates tcp_skb_cb/rate_sample and removes
# the legacy byte-based tx.in_flight assignment in tcp_output.c, but it omits
# the matching tcp_rate.c changes from the Google BBR tree.  Without these,
# tx.in_flight remains stale/zero and BBRv3 loss/ECN samples are incomplete.
if "void tcp_set_tx_in_flight(struct sock *sk, struct sk_buff *skb)" not in s:
    marker = "/* Snapshot the current delivery information in the skb, to generate\n"
    if marker not in s:
        raise SystemExit("tcp_rate: snapshot marker missing")
    helper = r'''void tcp_set_tx_in_flight(struct sock *sk, struct sk_buff *skb)
{
	struct tcp_sock *tp = tcp_sk(sk);
	u32 in_flight;

	/* Record packet-count flight state expected by BBRv3. */
	in_flight = tcp_packets_in_flight(tp) + tcp_skb_pcount(skb);
	if (WARN_ONCE(in_flight > TCPCB_IN_FLIGHT_MAX,
		      "insane in_flight %u cc %s mss %u "
		      "cwnd %u pif %u %u %u %u\n",
		      in_flight, inet_csk(sk)->icsk_ca_ops->name,
		      tp->mss_cache, tp->snd_cwnd,
		      tp->packets_out, tp->retrans_out,
		      tp->sacked_out, tp->lost_out))
		in_flight = TCPCB_IN_FLIGHT_MAX;
	TCP_SKB_CB(skb)->tx.in_flight = in_flight;
}

'''
    s = s.replace(marker, helper + marker, 1)

# Extend the existing 4.19 sent-snapshot path with the metadata fields that
# BBRv3 consumes.  skb_mstamp/tcp_mstamp are already microseconds in this tree.
old = """	TCP_SKB_CB(skb)->tx.first_tx_mstamp	= tp->first_tx_mstamp;
	TCP_SKB_CB(skb)->tx.delivered_mstamp	= tp->delivered_mstamp;
	TCP_SKB_CB(skb)->tx.delivered		= tp->delivered;
	TCP_SKB_CB(skb)->tx.is_app_limited	= tp->app_limited ? 1 : 0;
"""
new = """	TCP_SKB_CB(skb)->tx.first_tx_mstamp	= tp->first_tx_mstamp;
	TCP_SKB_CB(skb)->tx.delivered_mstamp	= tp->delivered_mstamp;
	TCP_SKB_CB(skb)->tx.delivered		= tp->delivered;
	TCP_SKB_CB(skb)->tx.delivered_ce	= tp->delivered_ce;
	TCP_SKB_CB(skb)->tx.lost		= tp->lost;
	TCP_SKB_CB(skb)->tx.is_app_limited	= tp->app_limited ? 1 : 0;
	tcp_set_tx_in_flight(sk, skb);
"""
if old in s:
    s = s.replace(old, new, 1)
elif new not in s:
    raise SystemExit("tcp_rate_skb_sent metadata anchor missing")

old = """		rs->prior_delivered  = scb->tx.delivered;
		rs->prior_mstamp     = scb->tx.delivered_mstamp;
		rs->is_app_limited   = scb->tx.is_app_limited;
		rs->is_retrans	     = scb->sacked & TCPCB_RETRANS;
"""
new = """		rs->prior_lost	     = scb->tx.lost;
		rs->prior_delivered_ce = scb->tx.delivered_ce;
		rs->prior_delivered  = scb->tx.delivered;
		rs->prior_mstamp     = scb->tx.delivered_mstamp;
		rs->tx_in_flight     = scb->tx.in_flight;
		rs->last_end_seq     = scb->end_seq;
		rs->is_app_limited   = scb->tx.is_app_limited;
		rs->is_retrans	     = scb->sacked & TCPCB_RETRANS;
"""
if old in s:
    s = s.replace(old, new, 1)
elif "rs->tx_in_flight     = scb->tx.in_flight;" not in s:
    raise SystemExit("tcp_rate_skb_delivered metadata anchor missing")

# The BBRv3 patch intentionally shrinks the per-skb timestamps to 32 bits.
# Use wrap-safe 32-bit deltas when generating rate samples.
s = s.replace(
    "rs->interval_us      = tcp_stamp_us_delta(\n"
    "\t\t\t\t\t\tskb->skb_mstamp,\n"
    "\t\t\t\t\t\tscb->tx.first_tx_mstamp);",
    "rs->interval_us      = tcp_stamp32_us_delta(\n"
    "\t\t\t\t\t\t(u32)skb->skb_mstamp,\n"
    "\t\t\t\t\t\tscb->tx.first_tx_mstamp);",
    1,
)

old = """	rs->delivered   = tp->delivered - rs->prior_delivered;

	/* Model sending data and receiving ACKs as separate pipeline phases
"""
new = """	rs->delivered   = tp->delivered - rs->prior_delivered;
	rs->lost        = tp->lost - rs->prior_lost;
	rs->delivered_ce = tp->delivered_ce - rs->prior_delivered_ce;
	rs->delivered_ce &= TCPCB_DELIVERED_CE_MASK;

	/* Model sending data and receiving ACKs as separate pipeline phases
"""
if old in s:
    s = s.replace(old, new, 1)
elif "rs->lost        = tp->lost - rs->prior_lost;" not in s:
    raise SystemExit("tcp_rate_gen BBRv3 loss metadata anchor missing")

old = """	ack_us = tcp_stamp_us_delta(tp->tcp_mstamp,
				    rs->prior_mstamp); /* ack phase */
"""
new = """	ack_us = tcp_stamp32_us_delta((u32)tp->tcp_mstamp,
				      (u32)rs->prior_mstamp); /* ack phase */
"""
if old in s:
    s = s.replace(old, new, 1)
elif "ack_us = tcp_stamp32_us_delta((u32)tp->tcp_mstamp," not in s:
    raise SystemExit("tcp_rate_gen timestamp anchor missing")

required = [
    "void tcp_set_tx_in_flight(struct sock *sk, struct sk_buff *skb)",
    "tcp_set_tx_in_flight(sk, skb);",
    "TCP_SKB_CB(skb)->tx.delivered_ce",
    "TCP_SKB_CB(skb)->tx.lost",
    "rs->tx_in_flight",
    "rs->prior_lost",
    "rs->prior_delivered_ce",
    "rs->lost        = tp->lost - rs->prior_lost;",
    "rs->delivered_ce = tp->delivered_ce - rs->prior_delivered_ce;",
]
for needle in required:
    if needle not in s:
        raise SystemExit(f"tcp_rate BBRv3 metadata missing: {needle}")

p.write_text(s)
PY

echo "==> Splitting BBRv1 and BBRv3 into separate congestion controls"
mv net/ipv4/tcp_bbr.c net/ipv4/tcp_bbr3.c
cp "$TMP/tcp_bbr_v1.c" net/ipv4/tcp_bbr.c

echo "==> Adapting retained BBRv1 to the BBRv3 TCP-core TSO callback API"
python3 - "$KERNEL/net/ipv4/tcp_bbr.c" <<'PY'
from pathlib import Path
import re, sys

p = Path(sys.argv[1])
s = p.read_text()

if "static u32 bbr_tso_segs(struct sock *sk, unsigned int mss_now)" not in s:
    pat = re.compile(
        r"(static u32 bbr_min_tso_segs\(struct sock \*sk\)\n"
        r"\{\n.*?\n\})",
        re.S,
    )
    m = pat.search(s)
    if not m:
        raise SystemExit("BBRv1 min_tso helper not found")
    wrapper = (
        m.group(1)
        + "\n\n"
        + "/* Preserve legacy BBRv1's min-TSO policy on the BBRv3 TCP core. */\n"
        + "static u32 bbr_tso_segs(struct sock *sk, unsigned int mss_now)\n"
        + "{\n"
        + "\tu32 bytes, segs;\n"
        + "\n"
        + "\t/* tcp_tso_autosize() is static in tcp_output.c, so mirror its\n"
        + "\t * calculation here and retain BBRv1's custom minimum segment count.\n"
        + "\t */\n"
        + "\tbytes = min_t(unsigned long,\n"
        + "\t\t      sk->sk_pacing_rate >> READ_ONCE(sk->sk_pacing_shift),\n"
        + "\t\t      sk->sk_gso_max_size - 1 - MAX_TCP_HEADER);\n"
        + "\tsegs = max_t(u32, bytes / mss_now, bbr_min_tso_segs(sk));\n"
        + "\treturn segs;\n"
        + "}"
    )
    s = s[:m.start()] + wrapper + s[m.end():]

if ".min_tso_segs" in s:
    s, n = re.subn(
        r"\.min_tso_segs\s*=\s*bbr_min_tso_segs,",
        ".tso_segs\t= bbr_tso_segs,",
        s,
        count=1,
    )
    if n != 1:
        raise SystemExit("BBRv1 congestion-ops min_tso callback anchor mismatch")

if ".min_tso_segs" in s:
    raise SystemExit("BBRv1 still references removed min_tso_segs congestion-op field")
if re.search(r"^\s*return\s+tcp_tso_autosize\s*\(", s, re.M):
    raise SystemExit("BBRv1 must not call static tcp_tso_autosize from tcp_output.c")
if ".tso_segs\t= bbr_tso_segs," not in s:
    raise SystemExit("BBRv1 tso_segs callback registration missing")

p.write_text(s)
PY

python3 - "$KERNEL" <<'PY'
from pathlib import Path
import re, sys

root = Path(sys.argv[1])
bbr3 = root / "net/ipv4/tcp_bbr3.c"
kconfig = root / "net/ipv4/Kconfig"
makefile = root / "net/ipv4/Makefile"
defconfig = root / "arch/arm64/configs/a52xq_defconfig"
sch_fq = root / "net/sched/sch_fq.c"
sch_generic_h = root / "include/net/sch_generic.h"
sch_mq = root / "net/sched/sch_mq.c"

s = bbr3.read_text()
if '#define BBR_VERSION' not in s or not re.search(r'#define\s+BBR_VERSION\s+3\b', s):
    raise SystemExit("BBRv3 source does not report BBR_VERSION 3")
if s.count('.name\t\t= "bbr",') != 1:
    raise SystemExit("unexpected BBRv3 congestion-control name anchor")
s = s.replace('.name\t\t= "bbr",', '.name\t\t= "bbr3",', 1)
s = s.replace('MODULE_DESCRIPTION("TCP BBR (Bottleneck Bandwidth and RTT)");',
              'MODULE_DESCRIPTION("TCP BBRv3 (Bottleneck Bandwidth and RTT)");')
bbr3.write_text(s)

m = makefile.read_text()
anchor = 'obj-$(CONFIG_TCP_CONG_BBR) += tcp_bbr.o\n'
if anchor not in m:
    raise SystemExit("Makefile BBR anchor missing")
if 'CONFIG_TCP_CONG_BBR3' not in m:
    m = m.replace(anchor, anchor + 'obj-$(CONFIG_TCP_CONG_BBR3) += tcp_bbr3.o\n', 1)
makefile.write_text(m)

k = kconfig.read_text()
if 'config TCP_CONG_BBR3' not in k:
    pos = k.find('\nchoice\n', k.find('config TCP_CONG_BBR'))
    if pos < 0:
        raise SystemExit("Kconfig congestion-control choice anchor missing")
    block = r'''
config TCP_CONG_BBR3
	tristate "BBRv3 TCP"
	default n
	---help---
	  Google BBRv3 congestion control, selectively backported to Linux 4.19.
	  It uses a model of bottleneck bandwidth and RTT, plus loss and ECN
	  signals, to improve throughput, queueing latency, and coexistence.
	  This A52 port is registered separately as "bbr3" so the original
	  Linux/Samsung BBRv1 implementation remains available as "bbr".
	  FQ is recommended for efficient pacing.

'''
    k = k[:pos] + '\n' + block + k[pos:]
# Make BBRv3 a first-class default choice, while retaining BBRv1/BIC.
if 'config DEFAULT_BBR3' not in k:
    anchor = '\tconfig DEFAULT_BBR\n\t\tbool "BBR" if TCP_CONG_BBR=y\n'
    if anchor not in k:
        raise SystemExit("Kconfig DEFAULT_BBR anchor missing")
    k = k.replace(
        anchor,
        anchor + '\n\tconfig DEFAULT_BBR3\n\t\tbool "BBRv3" if TCP_CONG_BBR3=y\n',
        1,
    )
if 'default "bbr3" if DEFAULT_BBR3' not in k:
    anchor = '\tdefault "bbr" if DEFAULT_BBR\n'
    if anchor not in k:
        raise SystemExit("Kconfig DEFAULT_TCP_CONG BBR anchor missing")
    k = k.replace(anchor, anchor + '\tdefault "bbr3" if DEFAULT_BBR3\n', 1)
kconfig.write_text(k)

d = defconfig.read_text()
old = '# CONFIG_TCP_CONG_BBR is not set\n'
if old in d:
    d = d.replace(old, 'CONFIG_TCP_CONG_BBR=y\nCONFIG_TCP_CONG_BBR3=y\n', 1)
elif 'CONFIG_TCP_CONG_BBR=y' in d and 'CONFIG_TCP_CONG_BBR3=y' not in d:
    d = d.replace('CONFIG_TCP_CONG_BBR=y\n',
                  'CONFIG_TCP_CONG_BBR=y\nCONFIG_TCP_CONG_BBR3=y\n', 1)
elif 'CONFIG_TCP_CONG_BBR3=y' not in d:
    raise SystemExit("defconfig BBR anchor missing")

fq_off = '# CONFIG_NET_SCH_FQ is not set\n'
if fq_off in d:
    d = d.replace(fq_off, 'CONFIG_NET_SCH_FQ=y\n', 1)
elif 'CONFIG_NET_SCH_FQ=y' not in d:
    raise SystemExit("defconfig FQ anchor missing")

# Promote the validated BBRv3 implementation to the boot-time default.
# Handle the current Samsung default explicitly and keep the edit deterministic.
if 'CONFIG_DEFAULT_BIC=y\n' in d:
    d = d.replace('CONFIG_DEFAULT_BIC=y\n', '# CONFIG_DEFAULT_BIC is not set\n', 1)
if '# CONFIG_DEFAULT_BBR3 is not set\n' in d:
    d = d.replace('# CONFIG_DEFAULT_BBR3 is not set\n', 'CONFIG_DEFAULT_BBR3=y\n', 1)
elif 'CONFIG_DEFAULT_BBR3=y\n' not in d:
    anchor = '# CONFIG_DEFAULT_RENO is not set\n'
    if anchor not in d:
        raise SystemExit("defconfig default congestion-control anchor missing")
    d = d.replace(anchor, 'CONFIG_DEFAULT_BBR3=y\n' + anchor, 1)

if 'CONFIG_DEFAULT_TCP_CONG="bic"\n' in d:
    d = d.replace('CONFIG_DEFAULT_TCP_CONG="bic"\n',
                  'CONFIG_DEFAULT_TCP_CONG="bbr3"\n', 1)
elif 'CONFIG_DEFAULT_TCP_CONG="bbr3"\n' not in d:
    raise SystemExit("defconfig DEFAULT_TCP_CONG anchor missing")

defconfig.write_text(d)

# Expose FQ's qdisc ops to the generic scheduler so Wi-Fi mq queues can
# instantiate FQ directly without userspace tc intervention.
fq = sch_fq.read_text()
if 'static struct Qdisc_ops fq_qdisc_ops __read_mostly' in fq:
    fq = fq.replace('static struct Qdisc_ops fq_qdisc_ops __read_mostly',
                    'struct Qdisc_ops fq_qdisc_ops __read_mostly', 1)
if 'static struct Qdisc_ops fq_qdisc_ops __read_mostly' in fq:
    raise SystemExit("sch_fq: fq_qdisc_ops still static")
if 'struct Qdisc_ops fq_qdisc_ops __read_mostly' not in fq:
    raise SystemExit("sch_fq: fq_qdisc_ops declaration missing")

# Built-in Wi-Fi may instantiate FQ during device registration.  Ensure the
# FQ flow slab and qdisc registration exist before device_initcall drivers.
if '#ifndef MODULE\nsubsys_initcall(fq_module_init);' not in fq:
    if 'module_init(fq_module_init)\n' not in fq:
        raise SystemExit("sch_fq: module_init anchor missing")
    fq = fq.replace(
        'module_init(fq_module_init)\n',
        '#ifndef MODULE\n'
        'subsys_initcall(fq_module_init);\n'
        '#else\n'
        'module_init(fq_module_init)\n'
        '#endif\n',
        1,
    )
sch_fq.write_text(fq)

h = sch_generic_h.read_text()
if 'extern struct Qdisc_ops fq_qdisc_ops;' not in h:
    anchor = 'extern struct Qdisc_ops mq_qdisc_ops;\n'
    if anchor not in h:
        raise SystemExit("sch_generic: mq qdisc declaration anchor missing")
    h = h.replace(anchor,
                  anchor + '#ifdef CONFIG_NET_SCH_FQ\n'
                           'extern struct Qdisc_ops fq_qdisc_ops;\n'
                           '#endif\n',
                  1)

old = '''static inline const struct Qdisc_ops *
get_default_qdisc_ops(const struct net_device *dev, int ntx)
{
	return ntx < dev->real_num_tx_queues ?
			default_qdisc_ops : &pfifo_fast_ops;
}
'''
new = '''static inline const struct Qdisc_ops *
get_default_qdisc_ops(const struct net_device *dev, int ntx)
{
	if (ntx >= dev->real_num_tx_queues)
		return &pfifo_fast_ops;

#ifdef CONFIG_NET_SCH_FQ
	/*
	 * A52 Wi-Fi devices publish ieee80211_ptr before registration.
	 * Keep Samsung's mq root/hardware queues, but use FQ as each active
	 * Wi-Fi TX queue's default leaf.  Non-Wi-Fi devices (including rmnet)
	 * retain the existing global default until separately validated.
	 */
	if (dev->ieee80211_ptr)
		return &fq_qdisc_ops;
#endif

	return default_qdisc_ops;
}
'''
if old in h:
    h = h.replace(old, new, 1)
elif new not in h:
    raise SystemExit("sch_generic: default qdisc selector anchor missing")
sch_generic_h.write_text(h)

mq = sch_mq.read_text()
needle = '''	if (!netif_is_multiqueue(dev))
		return -EOPNOTSUPP;

	/* pre-allocate qdiscs, attachment can't fail */
'''
replacement = '''	if (!netif_is_multiqueue(dev))
		return -EOPNOTSUPP;

#ifdef CONFIG_NET_SCH_FQ
	/*
	 * The stock auto-created mq root has handle 0:, which prevents old
	 * Android tc from addressing its child classes.  Our hardware test
	 * proved mq 1: + FQ on 1:1..1:N works correctly.  Assign that handle
	 * natively for Wi-Fi before the child qdiscs are constructed.
	 */
	if (!sch->handle && dev->ieee80211_ptr)
		sch->handle = TC_H_MAKE(0x00010000U, 0);
#endif

	/* pre-allocate qdiscs, attachment can't fail */
'''
if needle in mq:
    mq = mq.replace(needle, replacement, 1)
elif replacement not in mq:
    raise SystemExit("sch_mq: init anchor missing")
sch_mq.write_text(mq)
PY

echo "==> Normalizing whitespace from upstream BBRv3 compatibility patch"
python3 - "$KERNEL" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
files = [
    "include/net/tcp.h",
    "include/uapi/linux/rtnetlink.h",
    "net/core/sock.c",
    "net/ipv4/tcp_input.c",
    "net/sched/sch_fq.c",
]

for rel in files:
    p = root / rel
    if not p.exists():
        continue
    lines = p.read_text().splitlines()
    fixed = []
    for line in lines:
        line = line.rstrip()
        while line.startswith(" \t"):
            line = line[1:]
        fixed.append(line)
    p.write_text("\n".join(fixed) + "\n")
PY

echo "==> Auditing BBRv3 source split"
grep -Eq '#define[[:space:]]+BBR_VERSION[[:space:]]+3' net/ipv4/tcp_bbr3.c
grep -Fq '.name		= "bbr3",' net/ipv4/tcp_bbr3.c
grep -Fq '.name		= "bbr",' net/ipv4/tcp_bbr.c
grep -Fq 'config TCP_CONG_BBR3' net/ipv4/Kconfig
grep -Fq 'CONFIG_TCP_CONG_BBR3' net/ipv4/Makefile
grep -Fxq 'CONFIG_TCP_CONG_BBR=y' arch/arm64/configs/a52xq_defconfig
grep -Fxq 'CONFIG_TCP_CONG_BBR3=y' arch/arm64/configs/a52xq_defconfig
grep -Fxq 'CONFIG_NET_SCH_FQ=y' arch/arm64/configs/a52xq_defconfig
grep -Fxq 'CONFIG_DEFAULT_BBR3=y' arch/arm64/configs/a52xq_defconfig
grep -Fxq 'CONFIG_DEFAULT_TCP_CONG="bbr3"' arch/arm64/configs/a52xq_defconfig
grep -Fq 'config DEFAULT_BBR3' net/ipv4/Kconfig
grep -Fq 'default "bbr3" if DEFAULT_BBR3' net/ipv4/Kconfig
grep -Fq 'struct Qdisc_ops fq_qdisc_ops __read_mostly' net/sched/sch_fq.c
grep -Fq 'subsys_initcall(fq_module_init);' net/sched/sch_fq.c
grep -Fq 'if (dev->ieee80211_ptr)' include/net/sch_generic.h
grep -Fq 'sch->handle = TC_H_MAKE(0x00010000U, 0);' net/sched/sch_mq.c
grep -Fq 'tcp_plb_update_state' net/ipv4/tcp_plb.c
grep -Fq 'TCP_CONG_WANTS_CE_EVENTS' include/net/tcp.h
grep -Fq 'void tcp_set_tx_in_flight(struct sock *sk, struct sk_buff *skb)' net/ipv4/tcp_rate.c
grep -Fq 'tcp_set_tx_in_flight(sk, skb);' net/ipv4/tcp_rate.c
grep -Fq 'rs->tx_in_flight' net/ipv4/tcp_rate.c
grep -Fq 'rs->lost        = tp->lost - rs->prior_lost;' net/ipv4/tcp_rate.c
grep -Fq 'rs->delivered_ce = tp->delivered_ce - rs->prior_delivered_ce;' net/ipv4/tcp_rate.c

git diff --check

echo "BBRv3 network phase applied"
echo "bbrv1=bbr"
echo "bbrv3=bbr3"
echo "bbrv3_google_source_commit=$GOOGLE_BBR_COMMIT"
echo "bbrv3_4.19_compat_ref=$PATCH_REF"
echo "fq=enabled"
echo "default_cc=bbr3"
echo "wifi_qdisc=mq-1-with-fq-leaves"
