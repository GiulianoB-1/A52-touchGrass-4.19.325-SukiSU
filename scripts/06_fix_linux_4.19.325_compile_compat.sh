#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/compile-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying reviewed Linux $TARGET_VERSION compile compatibility repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []

# Linux 4.19.325 adds dev_parse_header_protocol(), while the Samsung
# header_ops layout retains two Android KABI reserve slots. Add the upstream
# callback in the live structure without dropping the vendor reserves.
path = root / "include/linux/netdevice.h"
text = path.read_text()
start = text.index("struct header_ops {")
end = text.index("\n};", start)
segment = text[start:end]
if "(*parse_protocol)" not in segment:
    anchor = "\tbool\t(*validate)(const char *ll_header, unsigned int len);\n"
    if segment.count(anchor) != 1:
        raise SystemExit("header_ops validate anchor mismatch")
    replacement = anchor + "\t__be16\t(*parse_protocol)(const struct sk_buff *skb);\n"
    text = text[:start] + segment.replace(anchor, replacement, 1) + text[end:]
    path.write_text(text)
    repairs.append("include/linux/netdevice.h=added-header_ops-parse_protocol")
elif segment.count("(*parse_protocol)") != 1:
    raise SystemExit("unexpected header_ops parse_protocol count")
if "ANDROID_KABI_RESERVE(1);" not in segment or "ANDROID_KABI_RESERVE(2);" not in segment:
    # Re-read after insertion because segment above represents the old text.
    current = path.read_text()
    current_segment = current[current.index("struct header_ops {"):current.index("\n};", current.index("struct header_ops {"))]
    if "ANDROID_KABI_RESERVE(1);" not in current_segment or "ANDROID_KABI_RESERVE(2);" not in current_segment:
        raise SystemExit("Samsung header_ops KABI reserves are missing")

# raw6_sock now embeds struct icmp6_filter. Keep the Samsung Android KABI
# include and add the UAPI declaration required by upstream 4.19.325.
path = root / "include/linux/ipv6.h"
text = path.read_text()
include = "#include <uapi/linux/icmpv6.h>\n"
if include not in text:
    anchor = "#include <uapi/linux/ipv6.h>\n"
    if text.count(anchor) != 1:
        raise SystemExit("IPv6 UAPI include anchor mismatch")
    path.write_text(text.replace(anchor, anchor + include, 1))
    repairs.append("include/linux/ipv6.h=added-uapi-icmpv6-include")

# touchGrass and upstream independently added the same clearbhb assembler
# macro. A clean textual merge retained both definitions; keep exactly one.
path = root / "arch/arm64/include/asm/assembler.h"
text = path.read_text()
block = (
    "/*\n"
    " * Clear Branch History instruction\n"
    " */\n"
    "\t.macro clearbhb\n"
    "\thint\t#22\n"
    "\t.endm\n"
)
count = text.count(block)
if count == 2:
    first = text.index(block)
    second = text.index(block, first + len(block))
    text = text[:second] + text[second + len(block):]
    while "\n\n\n/*\n * Speculation barrier" in text:
        text = text.replace(
            "\n\n\n/*\n * Speculation barrier",
            "\n\n/*\n * Speculation barrier",
        )
    path.write_text(text)
    repairs.append("arch/arm64/include/asm/assembler.h=removed-duplicate-clearbhb")
elif count != 1:
    raise SystemExit(f"unexpected clearbhb macro block count: {count}")

# Validate the exact postconditions.
net = (root / "include/linux/netdevice.h").read_text()
net_segment = net[net.index("struct header_ops {"):net.index("\n};", net.index("struct header_ops {"))]
if net_segment.count("(*parse_protocol)") != 1:
    raise SystemExit("header_ops parse_protocol repair failed")
if (root / "include/linux/ipv6.h").read_text().count(include) != 1:
    raise SystemExit("ICMPv6 include repair failed")
if (root / "arch/arm64/include/asm/assembler.h").read_text().count(block) != 1:
    raise SystemExit("clearbhb deduplication failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "Linux $TARGET_VERSION compile compatibility repairs applied"
