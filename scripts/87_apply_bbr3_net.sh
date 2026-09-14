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
    echo "ERROR: BBRv3 compatibility patch failed without .rej files (rc=$rc)" >&2
    exit "$rc"
fi

echo "==> Splitting BBRv1 and BBRv3 into separate congestion controls"
mv net/ipv4/tcp_bbr.c net/ipv4/tcp_bbr3.c
cp "$TMP/tcp_bbr_v1.c" net/ipv4/tcp_bbr.c

python3 - "$KERNEL" <<'PY'
from pathlib import Path
import re, sys

root = Path(sys.argv[1])
bbr3 = root / "net/ipv4/tcp_bbr3.c"
kconfig = root / "net/ipv4/Kconfig"
makefile = root / "net/ipv4/Makefile"
defconfig = root / "arch/arm64/configs/a52xq_defconfig"

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

defconfig.write_text(d)
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
grep -Fq 'tcp_plb_update_state' net/ipv4/tcp_plb.c
grep -Fq 'TCP_CONG_WANTS_CE_EVENTS' include/net/tcp.h

git diff --check

echo "BBRv3 network phase applied"
echo "bbrv1=bbr"
echo "bbrv3=bbr3"
echo "bbrv3_google_source_commit=$GOOGLE_BBR_COMMIT"
echo "bbrv3_4.19_compat_ref=$PATCH_REF"
echo "fq=enabled"
echo "default_cc=retained-existing"
