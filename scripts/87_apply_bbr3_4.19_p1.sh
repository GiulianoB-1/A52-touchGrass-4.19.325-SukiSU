#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <kernel-tree>" >&2
  exit 2
fi

KERNEL="$(cd "$1" && pwd)"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACTS="$PROJECT/artifacts"
mkdir -p "$ARTIFACTS"

BBR3_BACKPORT_REPO="kucingoranye/kernel_patches"
BBR3_BACKPORT_COMMIT="b249b8278b9691b73fc806ed379bd0c20c823b69"
BBR3_GOOGLE_BASE="cb31f3d02b1d7cd7cfdff4dd2b8b9d38879904af"
PATCH_URL="https://raw.githubusercontent.com/kucingoranye/kernel_patches/$BBR3_BACKPORT_COMMIT/tcp/bbr-v3-kernel-4.19.patch"
PATCH_FILE="$ARTIFACTS/bbr3-kernel-4.19-pinned.patch"
PATCH_LOG="$ARTIFACTS/bbr3-p1-patch.log"

echo "==> Fetching pinned Linux 4.19 BBRv3 backport"
curl -fL --retry 3 --retry-delay 2 "$PATCH_URL" -o "$PATCH_FILE"
grep -Fq "$BBR3_GOOGLE_BASE" "$PATCH_FILE"
grep -Fq "BBR_VERSION" "$PATCH_FILE"
sha256sum "$PATCH_FILE" | tee "$ARTIFACTS/bbr3-p1-patch.sha256"

backup="$(mktemp -d)"
trap 'rm -rf "$backup"' EXIT
cp "$KERNEL/net/ipv4/tcp_bbr.c" "$backup/tcp_bbr.c"
cp "$KERNEL/net/ipv4/Kconfig" "$backup/Kconfig"
cp "$KERNEL/net/ipv4/Makefile" "$backup/Makefile"

echo "==> Applying pinned 4.19 TCP-core BBRv3 compatibility backport"
set +e
(
  cd "$KERNEL"
  patch -p1 -F3 --batch --forward < "$PATCH_FILE"
) 2>&1 | tee "$PATCH_LOG"
rc=${PIPESTATUS[0]}
set -e

find "$KERNEL" -name '*.rej' -print | sort > "$ARTIFACTS/bbr3-p1-rejects.txt"
if [ "$rc" -ne 0 ] || [ -s "$ARTIFACTS/bbr3-p1-rejects.txt" ]; then
  echo "ERROR: BBRv3 compatibility patch has unresolved rejects on this Samsung 4.19.206 tree" >&2
  cat "$ARTIFACTS/bbr3-p1-rejects.txt" >&2 || true
  exit 1
fi

# The reference backport replaces BBRv1 in-place. We intentionally preserve
# Samsung/Linux BBRv1 as "bbr" and expose the new algorithm separately as
# "bbr3" for safe A/B testing.
cp "$KERNEL/net/ipv4/tcp_bbr.c" "$KERNEL/net/ipv4/tcp_bbr3.c"
cp "$backup/tcp_bbr.c" "$KERNEL/net/ipv4/tcp_bbr.c"
cp "$backup/Kconfig" "$KERNEL/net/ipv4/Kconfig"
cp "$backup/Makefile" "$KERNEL/net/ipv4/Makefile"

python3 - "$KERNEL" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
bbr3 = root / "net/ipv4/tcp_bbr3.c"
kconfig = root / "net/ipv4/Kconfig"
makefile = root / "net/ipv4/Makefile"
tcp_h = root / "include/net/tcp.h"
tcp_output = root / "net/ipv4/tcp_output.c"
defconfig = root / "arch/arm64/configs/a52xq_defconfig"

def once(text, old, new, name):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{name}: expected one anchor, found {n}")
    return text.replace(old, new, 1)

# Register the new implementation under a distinct congestion-control name.
s = bbr3.read_text()
s = once(s, '.name\t\t= "bbr",', '.name\t\t= "bbr3",', "bbr3 ca name")
s = s.replace('MODULE_DESCRIPTION("TCP BBR (Bottleneck Bandwidth and RTT)");',
              'MODULE_DESCRIPTION("TCP BBRv3 (Bottleneck Bandwidth and RTT)");')
if "#define BBR_VERSION" not in s or "3" not in s[s.index("#define BBR_VERSION"):s.index("#define BBR_VERSION")+80]:
    raise SystemExit("BBRv3 version marker missing")
bbr3.write_text(s)

# Preserve the 4.19 congestion-control API for BBRv1 and any vendor CC while
# also exposing the newer tso_segs() callback required by BBRv3.
s = tcp_h.read_text()
anchor = """\t/* pick target number of segments per TSO/GSO skb (optional): */
\tu32 (*tso_segs)(struct sock *sk, unsigned int mss_now);
"""
compat = """\t/* Legacy 4.19 override retained for existing congestion controls. */
\tu32 (*min_tso_segs)(struct sock *sk);
\t/* BBRv3/newer API: pick target number of segments per TSO/GSO skb. */
\tu32 (*tso_segs)(struct sock *sk, unsigned int mss_now);
"""
s = once(s, anchor, compat, "tcp_congestion_ops tso callback")
tcp_h.write_text(s)

s = tcp_output.read_text()
old = """static u32 tcp_tso_segs(struct sock *sk, unsigned int mss_now)
{
\tconst struct tcp_congestion_ops *ca_ops = inet_csk(sk)->icsk_ca_ops;
\tu32 tso_segs;

\ttso_segs = ca_ops->tso_segs ?
 \t\tca_ops->tso_segs(sk, mss_now) :
 \t\ttcp_tso_autosize(sk, mss_now,
 \t\t\t\t sock_net(sk)->ipv4.sysctl_tcp_min_tso_segs);
\treturn min_t(u32, tso_segs, sk->sk_gso_max_segs);
}
"""
# Be tolerant of whitespace in the reference patch while still requiring a
# unique function body.
if old not in s:
    start = s.find("static u32 tcp_tso_segs(struct sock *sk, unsigned int mss_now)")
    if start < 0:
        raise SystemExit("tcp_tso_segs function missing")
    end = s.find("\n}\n", start)
    if end < 0:
        raise SystemExit("tcp_tso_segs end missing")
    end += 3
    old = s[start:end]
new = """static u32 tcp_tso_segs(struct sock *sk, unsigned int mss_now)
{
\tconst struct tcp_congestion_ops *ca_ops = inet_csk(sk)->icsk_ca_ops;
\tu32 min_tso, tso_segs;

\tif (ca_ops->tso_segs)
\t\ttso_segs = ca_ops->tso_segs(sk, mss_now);
\telse {
\t\tmin_tso = ca_ops->min_tso_segs ?
\t\t\tca_ops->min_tso_segs(sk) :
\t\t\tREAD_ONCE(sock_net(sk)->ipv4.sysctl_tcp_min_tso_segs);
\t\ttso_segs = tcp_tso_autosize(sk, mss_now, min_tso);
\t}
\treturn min_t(u32, tso_segs, sk->sk_gso_max_segs);
}
"""
s = once(s, old, new, "tcp_tso_segs compat")
tcp_output.write_text(s)

# Add BBRv3 as a separate selectable congestion-control implementation.
s = kconfig.read_text()
choice = """choice
\tprompt "Default TCP congestion control"
"""
block = """config TCP_CONG_BBR3
\ttristate "BBRv3 TCP"
\tdefault n
\t---help---
\t  Enable Google TCP BBR version 3 as a separate congestion control
\t  named "bbr3". The legacy BBR implementation remains available as
\t  "bbr" for runtime A/B testing. BBRv3 uses TCP pacing; fq is strongly
\t  recommended when available.

"""
if "config TCP_CONG_BBR3" not in s:
    s = once(s, choice, block + choice, "BBR3 Kconfig")
kconfig.write_text(s)

s = makefile.read_text()
anchor = "obj-$(CONFIG_TCP_CONG_BBR) += tcp_bbr.o\n"
repl = anchor + "obj-$(CONFIG_TCP_CONG_BBR3) += tcp_bbr3.o tcp_plb.o\n"
if "CONFIG_TCP_CONG_BBR3" not in s:
    s = once(s, anchor, repl, "BBR3 Makefile")
makefile.write_text(s)

# Enable both algorithms plus fq, but intentionally leave Samsung's existing
# default congestion control untouched for the first hardware test.
s = defconfig.read_text()
s = s.replace("# CONFIG_TCP_CONG_BBR is not set", "CONFIG_TCP_CONG_BBR=y")
if "CONFIG_TCP_CONG_BBR3=y" not in s:
    s = s.replace("CONFIG_TCP_CONG_BBR=y", "CONFIG_TCP_CONG_BBR=y\nCONFIG_TCP_CONG_BBR3=y", 1)
s = s.replace("# CONFIG_NET_SCH_FQ is not set", "CONFIG_NET_SCH_FQ=y")
defconfig.write_text(s)

# Hard safety assertions: BIC remains the default in P1.
dc = defconfig.read_text()
required = [
    "CONFIG_TCP_CONG_BBR=y",
    "CONFIG_TCP_CONG_BBR3=y",
    "CONFIG_NET_SCH_FQ=y",
    'CONFIG_DEFAULT_TCP_CONG="bic"',
]
for needle in required:
    if needle not in dc:
        raise SystemExit(f"defconfig missing {needle}")
if 'CONFIG_DEFAULT_TCP_CONG="bbr3"' in dc:
    raise SystemExit("BBRv3 must not be the P1 default")

checks = {
    bbr3: ['#define BBR_VERSION', '.name\t\t= "bbr3",', "bbr_skb_marked_lost"],
    tcp_h: ["min_tso_segs", "tso_segs", "TCP_CONG_WANTS_CE_EVENTS"],
    root / "net/ipv4/tcp_plb.c": ["tcp_plb_update_state", "tcp_plb_check_rehash"],
    root / "include/uapi/linux/inet_diag.h": ["bbr_version", "bbr_inflight_hi"],
}
for path, needles in checks.items():
    if not path.exists():
        raise SystemExit(f"missing BBRv3 file {path}")
    txt = path.read_text()
    for needle in needles:
        if needle not in txt:
            raise SystemExit(f"{path}: missing {needle}")

print("BBRv3 P1 4.19 compatibility backport applied")
print("google_algorithm_base=cb31f3d02b1d7cd7cfdff4dd2b8b9d38879904af")
print("linux419_backport=b249b8278b9691b73fc806ed379bd0c20c823b69")
print("legacy_bbr=bbr")
print("bbrv3=bbr3")
print("fq=enabled")
print("default_congestion_control=bic_unchanged")
PY

git -C "$KERNEL" diff --check

{
  echo "phase=bbr3-p1"
  echo "google_algorithm_base=$BBR3_GOOGLE_BASE"
  echo "linux419_backport_repo=$BBR3_BACKPORT_REPO"
  echo "linux419_backport_commit=$BBR3_BACKPORT_COMMIT"
  echo "legacy_bbr=bbr"
  echo "bbrv3=bbr3"
  echo "fq=enabled"
  echo "default_cc=bic"
} | tee "$ARTIFACTS/bbr3-p1-source-audit.txt"
