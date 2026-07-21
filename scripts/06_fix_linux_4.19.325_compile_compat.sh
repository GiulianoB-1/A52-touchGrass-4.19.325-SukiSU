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


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: {count}")
    return text.replace(old, new, 1)


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
    replacement = anchor + "\t__be16\t(*parse_protocol)(const struct sk_buff *skb);\n"
    segment = replace_once(segment, anchor, replacement, "header_ops validate")
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("include/linux/netdevice.h=added-header_ops-parse_protocol")
elif segment.count("(*parse_protocol)") != 1:
    raise SystemExit("unexpected header_ops parse_protocol count")
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
    text = replace_once(text, anchor, anchor + include, "IPv6 UAPI include")
    path.write_text(text)
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

# The combined BPF verifier retained Android socket-context conversion logic,
# but lost its local converter typedef and also kept the old PTR_TO_CTX-only
# guard. Restore the Android declaration and allow the switch to select the
# correct converter for context, socket, and TCP-socket pointer types.
path = root / "kernel/bpf/verifier.c"
text = path.read_text()
func_start = text.index("static int convert_ctx_accesses(struct bpf_verifier_env *env)")
func_end = text.index("\nstatic int jit_subprogs", func_start)
segment = text[func_start:func_end]
loop_anchor = "\tfor (i = 0; i < insn_cnt; i++, insn++) {\n\t\tbool ctx_access;\n"
if "bpf_convert_ctx_access_t convert_ctx_access;" not in segment:
    replacement = loop_anchor + "\t\tbpf_convert_ctx_access_t convert_ctx_access;\n"
    segment = replace_once(segment, loop_anchor, replacement, "BPF converter declaration")
    repairs.append("kernel/bpf/verifier.c=restored-convert-ctx-access-typedef")
old_guard = (
    "\t\tif (env->insn_aux_data[i + delta].ptr_type != PTR_TO_CTX)\n"
    "\t\t\tcontinue;\n"
)
if old_guard in segment:
    segment = replace_once(segment, old_guard, "", "BPF obsolete PTR_TO_CTX guard")
    repairs.append("kernel/bpf/verifier.c=removed-obsolete-ptr-to-ctx-guard")
text = text[:func_start] + segment + text[func_end:]

# The Android verifier backport uses src in one diagnostic but the direct
# merge dropped the local register number. Restore it beside dst.
adjust_start = text.index("static int adjust_ptr_min_max_vals(")
adjust_end = text.index("\nstatic int adjust_reg_min_max_vals", adjust_start)
adjust = text[adjust_start:adjust_end]
if "u32 dst = insn->dst_reg, src = insn->src_reg;" not in adjust:
    old = "\tu32 dst = insn->dst_reg;\n"
    new = "\tu32 dst = insn->dst_reg, src = insn->src_reg;\n"
    adjust = replace_once(adjust, old, new, "BPF src register declaration")
    repairs.append("kernel/bpf/verifier.c=restored-src-register-number")
text = text[:adjust_start] + adjust + text[adjust_end:]
path.write_text(text)

# Keep Samsung's established capability numbering and consume three of the
# explicitly reserved slots for the late 4.19 stable ARM64 capabilities.
path = root / "arch/arm64/include/asm/cpucaps.h"
text = path.read_text()
cap_defs = (
    "#define ARM64_WORKAROUND_1742098\t\t39\n"
    "#define ARM64_HAS_SB\t\t\t\t40\n"
    "#define ARM64_WORKAROUND_SPECULATIVE_SSBS\t41\n"
)
if "ARM64_WORKAROUND_1742098" not in text:
    anchor = "#define ARM64_SPECTRE_BHB\t\t\t38\n\n"
    text = replace_once(text, anchor, anchor + cap_defs + "\n", "ARM64 reserved capability insertion")
    repairs.append("arch/arm64/include/asm/cpucaps.h=assigned-stable-capabilities-39-41")
for symbol in (
    "ARM64_WORKAROUND_1742098",
    "ARM64_HAS_SB",
    "ARM64_WORKAROUND_SPECULATIVE_SSBS",
):
    if text.count(symbol) != 1:
        raise SystemExit(f"unexpected {symbol} count in cpucaps.h")
if "#define ARM64_NCAPS\t\t\t\t62" not in text:
    raise SystemExit("Samsung ARM64_NCAPS reserve boundary changed")
path.write_text(text)

# Ensure the MIDR names referenced by the 3194386 stable list exist. Most are
# merged cleanly from upstream, but add any missing late-core identifiers using
# the exact architectural part numbers from Linux 4.19.325.
path = root / "arch/arm64/include/asm/cputype.h"
text = path.read_text()
part_defs = [
    ("ARM_CPU_PART_CORTEX_A715", "0xD4D"),
    ("ARM_CPU_PART_CORTEX_X1C", "0xD4C"),
    ("ARM_CPU_PART_CORTEX_X3", "0xD4E"),
    ("ARM_CPU_PART_NEOVERSE_V2", "0xD4F"),
    ("ARM_CPU_PART_CORTEX_A720", "0xD81"),
    ("ARM_CPU_PART_CORTEX_X4", "0xD82"),
    ("ARM_CPU_PART_NEOVERSE_V3", "0xD84"),
    ("ARM_CPU_PART_CORTEX_X925", "0xD85"),
    ("ARM_CPU_PART_CORTEX_A725", "0xD87"),
    ("ARM_CPU_PART_NEOVERSE_N3", "0xD8E"),
]
missing_parts = [f"#define {name}\t{value}\n" for name, value in part_defs if name not in text]
if missing_parts:
    anchor = "#define APM_CPU_PART_POTENZA"
    pos = text.index(anchor)
    text = text[:pos] + "".join(missing_parts) + "\n" + text[pos:]
    repairs.append("arch/arm64/include/asm/cputype.h=added-late-arm-part-numbers")
midr_defs = [
    ("MIDR_CORTEX_A715", "ARM_CPU_PART_CORTEX_A715"),
    ("MIDR_CORTEX_X1C", "ARM_CPU_PART_CORTEX_X1C"),
    ("MIDR_CORTEX_X3", "ARM_CPU_PART_CORTEX_X3"),
    ("MIDR_NEOVERSE_V2", "ARM_CPU_PART_NEOVERSE_V2"),
    ("MIDR_CORTEX_A720", "ARM_CPU_PART_CORTEX_A720"),
    ("MIDR_CORTEX_X4", "ARM_CPU_PART_CORTEX_X4"),
    ("MIDR_NEOVERSE_V3", "ARM_CPU_PART_NEOVERSE_V3"),
    ("MIDR_CORTEX_X925", "ARM_CPU_PART_CORTEX_X925"),
    ("MIDR_CORTEX_A725", "ARM_CPU_PART_CORTEX_A725"),
    ("MIDR_NEOVERSE_N3", "ARM_CPU_PART_NEOVERSE_N3"),
]
missing_midrs = [
    f"#define {name} MIDR_CPU_MODEL(ARM_CPU_IMP_ARM, {part})\n"
    for name, part in midr_defs
    if name not in text
]
if missing_midrs:
    anchor = "#define MIDR_THUNDERX"
    pos = text.index(anchor)
    text = text[:pos] + "".join(missing_midrs) + "\n" + text[pos:]
    repairs.append("arch/arm64/include/asm/cputype.h=added-late-arm-midr-values")
path.write_text(text)

# The stable errata table entries were merged, but their matching MIDR lists
# were not. Add the exact Linux 4.19.325 lists before arm64_errata[].
path = root / "arch/arm64/kernel/cpu_errata.c"
text = path.read_text()
insert_anchor = "const struct arm64_cpu_capabilities arm64_errata[] = {\n"
blocks = []
if "static struct midr_range broken_aarch32_aes[]" not in text:
    blocks.append(
        "#ifdef CONFIG_ARM64_ERRATUM_1742098\n"
        "static struct midr_range broken_aarch32_aes[] = {\n"
        "\tMIDR_RANGE(MIDR_CORTEX_A57, 0, 1, 0xf, 0xf),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A72),\n"
        "\t{},\n"
        "};\n"
        "#endif\n\n"
    )
if "static const struct midr_range erratum_spec_ssbs_list[]" not in text:
    blocks.append(
        "#ifdef CONFIG_ARM64_ERRATUM_3194386\n"
        "static const struct midr_range erratum_spec_ssbs_list[] = {\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A76),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A77),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A78),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A78C),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A710),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A715),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A720),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_A725),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_X1),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_X1C),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_X2),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_X3),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_X4),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_CORTEX_X925),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_NEOVERSE_N1),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_NEOVERSE_N2),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_NEOVERSE_N3),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_NEOVERSE_V1),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_NEOVERSE_V2),\n"
        "\tMIDR_ALL_VERSIONS(MIDR_NEOVERSE_V3),\n"
        "\t{}\n"
        "};\n"
        "#endif\n\n"
    )
if blocks:
    text = replace_once(text, insert_anchor, "".join(blocks) + insert_anchor, "ARM64 errata list insertion")
    repairs.append("arch/arm64/kernel/cpu_errata.c=restored-stable-errata-midr-lists")
path.write_text(text)

# Validate exact postconditions.
net = (root / "include/linux/netdevice.h").read_text()
net_segment = net[net.index("struct header_ops {"):net.index("\n};", net.index("struct header_ops {"))]
if net_segment.count("(*parse_protocol)") != 1:
    raise SystemExit("header_ops parse_protocol repair failed")
if (root / "include/linux/ipv6.h").read_text().count(include) != 1:
    raise SystemExit("ICMPv6 include repair failed")
if (root / "arch/arm64/include/asm/assembler.h").read_text().count(block) != 1:
    raise SystemExit("clearbhb deduplication failed")
verifier = (root / "kernel/bpf/verifier.c").read_text()
convert = verifier[verifier.index("static int convert_ctx_accesses"):verifier.index("\nstatic int jit_subprogs", verifier.index("static int convert_ctx_accesses"))]
if convert.count("bpf_convert_ctx_access_t convert_ctx_access;") != 1:
    raise SystemExit("BPF converter declaration repair failed")
if old_guard in convert:
    raise SystemExit("obsolete BPF PTR_TO_CTX guard remains")
adjust = verifier[verifier.index("static int adjust_ptr_min_max_vals("):verifier.index("\nstatic int adjust_reg_min_max_vals", verifier.index("static int adjust_ptr_min_max_vals("))]
if adjust.count("u32 dst = insn->dst_reg, src = insn->src_reg;") != 1:
    raise SystemExit("BPF src register repair failed")
caps = (root / "arch/arm64/include/asm/cpucaps.h").read_text()
for symbol in ("ARM64_WORKAROUND_1742098", "ARM64_HAS_SB", "ARM64_WORKAROUND_SPECULATIVE_SSBS"):
    if caps.count(symbol) != 1:
        raise SystemExit(f"ARM64 capability repair failed for {symbol}")
errata = (root / "arch/arm64/kernel/cpu_errata.c").read_text()
if errata.count("static struct midr_range broken_aarch32_aes[]") != 1:
    raise SystemExit("ARM64 1742098 list repair failed")
if errata.count("static const struct midr_range erratum_spec_ssbs_list[]") != 1:
    raise SystemExit("ARM64 3194386 list repair failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "Linux $TARGET_VERSION compile compatibility repairs applied"
