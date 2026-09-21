#!/usr/bin/env python3
from pathlib import Path
import re
import sys


def die(msg: str) -> None:
    raise SystemExit(f"BPF BOOTFIX P1 ERROR: {msg}")


def apply_sub(text: str, pattern: str, repl: str, label: str, *, flags: int = 0,
              required: bool = False) -> tuple[str, int]:
    out, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count > 1:
        die(f"{label}: matched more than once")
    if required and count != 1:
        die(f"{label}: expected one match, found {count}")
    return out, count


def replace_once_or_fixed(text: str, old: str, new: str, fixed_marker: str,
                          label: str) -> tuple[str, str]:
    count = text.count(old)
    if count == 1:
        return text.replace(old, new, 1), "patched"
    if count > 1:
        die(f"{label}: old form matched {count} times")
    if fixed_marker in text:
        return text, "already-fixed"
    die(f"{label}: neither buggy nor fixed form was found")


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: 129_apply_bpf_bootfix_p1.py <kernel-dir> <report-dir>")

    kernel = Path(sys.argv[1]).resolve()
    report_dir = Path(sys.argv[2]).resolve()
    header_path = kernel / "include/linux/bpf_verifier.h"
    verifier_path = kernel / "kernel/bpf/verifier.c"
    syscall_path = kernel / "kernel/bpf/syscall.c"

    for path in (header_path, verifier_path, syscall_path):
        if not path.is_file():
            die(f"missing {path}")

    report_dir.mkdir(parents=True, exist_ok=True)
    before = {
        header_path: header_path.read_text(),
        verifier_path: verifier_path.read_text(),
        syscall_path: syscall_path.read_text(),
    }
    header = before[header_path]
    verifier = before[verifier_path]
    syscall = before[syscall_path]
    status: list[tuple[str, str]] = []

    # 1. Remove touchGrass REG_LIVE_DONE. The bit was added and tested but
    # never set, so it is not a valid liveness state in this backport.
    header2, n = apply_sub(
        header,
        r"^[ \t]*REG_LIVE_DONE[ \t]*=[ \t]*4,[^\n]*\n",
        "",
        "remove REG_LIVE_DONE enum",
        flags=re.MULTILINE,
    )
    header = header2
    status.append(("REG_LIVE_DONE enum", "patched" if n else "already-fixed"))

    for label, pattern in (
        (
            "REG_LIVE_DONE register traversal",
            r"[ \t]*/\* stop traversal if already fully propagated upward \*/\n"
            r"[ \t]*if \(parent->frame\[parent->curframe\]->regs\[regno\]\.live & REG_LIVE_DONE\)\n"
            r"[ \t]*break;\n",
        ),
        (
            "REG_LIVE_DONE stack traversal",
            r"[ \t]*/\* stop traversal if already fully propagated \*/\n"
            r"[ \t]*if \(parent->frame\[frameno\]->stack\[slot\]\.spilled_ptr\.live & REG_LIVE_DONE\)\n"
            r"[ \t]*break;\n",
        ),
    ):
        verifier2, n = apply_sub(verifier, pattern, "", label)
        verifier = verifier2
        status.append((label, "patched" if n else "already-fixed"))

    # 2. Unify the verifier ID map with the header's official backported type.
    id_block = (
        r"/\* Maximum number of register states that can exist at once \*/\n"
        r"#define ID_MAP_SIZE[^\n]*\n"
        r"struct idpair \{\n"
        r"[ \t]*u32 old;\n"
        r"[ \t]*u32 cur;\n"
        r"\};\n\n"
    )
    verifier2, n = apply_sub(verifier, id_block, "", "remove duplicate verifier id map")
    verifier = verifier2
    status.append(("duplicate verifier id map", "patched" if n else "already-fixed"))

    verifier, s = replace_once_or_fixed(
        verifier,
        "static bool check_ids(u32 old_id, u32 cur_id, struct idpair *idmap)",
        "static bool check_ids(u32 old_id, u32 cur_id, struct bpf_id_pair *idmap)",
        "static bool check_ids(u32 old_id, u32 cur_id, struct bpf_id_pair *idmap)",
        "check_ids bpf_id_pair type",
    )
    status.append(("check_ids bpf_id_pair type", s))

    verifier, s = replace_once_or_fixed(
        verifier,
        "for (i = 0; i < ID_MAP_SIZE; i++)",
        "for (i = 0; i < BPF_ID_MAP_SIZE; i++)",
        "for (i = 0; i < BPF_ID_MAP_SIZE; i++)",
        "check_ids BPF_ID_MAP_SIZE",
    )
    status.append(("check_ids BPF_ID_MAP_SIZE", s))

    # 3. Restore the state-pruning guard paired with explore_alu_limits.
    guard = "\tcase SCALAR_VALUE:\n\t\tif (env->explore_alu_limits)\n\t\t\treturn false;\n\t\tif (rcur->type == SCALAR_VALUE) {"
    if guard in verifier:
        status.append(("explore_alu_limits pruning guard", "already-fixed"))
    else:
        old = "\tcase SCALAR_VALUE:\n\t\tif (rcur->type == SCALAR_VALUE) {"
        if verifier.count(old) != 1:
            die("explore_alu_limits pruning guard: expected one insertion anchor")
        verifier = verifier.replace(old, guard, 1)
        status.append(("explore_alu_limits pruning guard", "patched"))

    # 4. Critical boot fix: ctx_access is read by convert_ctx_accesses() but
    # touchGrass's partial backport never initialized it. That is undefined
    # behavior in the verifier and can vary with kernel stack contents.
    read_old = (
        "\t\tif (insn->code == (BPF_LDX | BPF_MEM | BPF_B) ||\n"
        "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_H) ||\n"
        "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_W) ||\n"
        "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_DW))\n"
        "\t\t\ttype = BPF_READ;\n"
    )
    read_new = (
        "\t\tif (insn->code == (BPF_LDX | BPF_MEM | BPF_B) ||\n"
        "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_H) ||\n"
        "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_W) ||\n"
        "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_DW)) {\n"
        "\t\t\ttype = BPF_READ;\n"
        "\t\t\tctx_access = true;\n"
        "\t\t}\n"
    )
    verifier, s = replace_once_or_fixed(
        verifier, read_old, read_new, "ctx_access = true;", "initialize ctx_access for reads"
    )
    status.append(("ctx_access read initialization", s))

    write_old = (
        "\t\telse if (insn->code == (BPF_STX | BPF_MEM | BPF_B) ||\n"
        "\t\t\t insn->code == (BPF_STX | BPF_MEM | BPF_H) ||\n"
        "\t\t\t insn->code == (BPF_STX | BPF_MEM | BPF_W) ||\n"
        "\t\t\t insn->code == (BPF_STX | BPF_MEM | BPF_DW))\n"
        "\t\t\ttype = BPF_WRITE;\n"
        "\t\telse\n"
        "\t\t\tcontinue;\n"
    )
    write_new = (
        "\t\telse if (insn->code == (BPF_STX | BPF_MEM | BPF_B) ||\n"
        "\t\t\t insn->code == (BPF_STX | BPF_MEM | BPF_H) ||\n"
        "\t\t\t insn->code == (BPF_STX | BPF_MEM | BPF_W) ||\n"
        "\t\t\t insn->code == (BPF_STX | BPF_MEM | BPF_DW)) {\n"
        "\t\t\ttype = BPF_WRITE;\n"
        "\t\t\tctx_access = BPF_CLASS(insn->code) == BPF_STX;\n"
        "\t\t} else {\n"
        "\t\t\tcontinue;\n"
        "\t\t}\n"
    )
    verifier, s = replace_once_or_fixed(
        verifier,
        write_old,
        write_new,
        "ctx_access = BPF_CLASS(insn->code) == BPF_STX;",
        "initialize ctx_access for writes",
    )
    status.append(("ctx_access write initialization", s))

    # 5. Repair the malformed attach-type cherry-pick. The buggy switch uses
    # 'prog' before bpf_prog_get_type() initializes it and leaves ptype unset.
    syscall, s = replace_once_or_fixed(
        syscall,
        "\tcase BPF_SK_MSG_VERDICT:\n\t\tret = sock_map_get_from_fd(attr, prog);\n\t\tbreak;",
        "\tcase BPF_SK_MSG_VERDICT:\n\t\tptype = BPF_PROG_TYPE_SK_MSG;\n\t\tbreak;",
        "\tcase BPF_SK_MSG_VERDICT:\n\t\tptype = BPF_PROG_TYPE_SK_MSG;\n\t\tbreak;",
        "BPF_SK_MSG_VERDICT attach type",
    )
    status.append(("BPF_SK_MSG_VERDICT attach type", s))

    syscall, s = replace_once_or_fixed(
        syscall,
        "\tcase BPF_SK_SKB_STREAM_PARSER:\n\tcase BPF_SK_SKB_STREAM_VERDICT:\n\t\tret = sock_map_get_from_fd(attr, prog);\n\t\tbreak;",
        "\tcase BPF_SK_SKB_STREAM_PARSER:\n\tcase BPF_SK_SKB_STREAM_VERDICT:\n\t\tptype = BPF_PROG_TYPE_SK_SKB;\n\t\tbreak;",
        "\tcase BPF_SK_SKB_STREAM_PARSER:\n\tcase BPF_SK_SKB_STREAM_VERDICT:\n\t\tptype = BPF_PROG_TYPE_SK_SKB;\n\t\tbreak;",
        "BPF_SK_SKB attach type",
    )
    status.append(("BPF_SK_SKB attach type", s))

    syscall, s = replace_once_or_fixed(
        syscall,
        "\tcase BPF_LIRC_MODE2:\n\t\tptype = BPF_PROG_TYPE_LIRC_MODE2;\n\tcase BPF_CGROUP_SYSCTL:",
        "\tcase BPF_LIRC_MODE2:\n\t\tptype = BPF_PROG_TYPE_LIRC_MODE2;\n\t\tbreak;\n\tcase BPF_CGROUP_SYSCTL:",
        "\tcase BPF_LIRC_MODE2:\n\t\tptype = BPF_PROG_TYPE_LIRC_MODE2;\n\t\tbreak;\n\tcase BPF_CGROUP_SYSCTL:",
        "BPF_LIRC_MODE2 break",
    )
    status.append(("BPF_LIRC_MODE2 break", s))

    # Strict post-fix audit. Refuse to write a partially repaired tree.
    if "REG_LIVE_DONE" in header or "REG_LIVE_DONE" in verifier:
        die("REG_LIVE_DONE remains")
    if "struct idpair" in verifier:
        die("legacy struct idpair remains")
    if re.search(r"(?<!BPF_)ID_MAP_SIZE", verifier):
        die("legacy ID_MAP_SIZE remains")
    required_verifier = (
        "static bool check_ids(u32 old_id, u32 cur_id, struct bpf_id_pair *idmap)",
        "for (i = 0; i < BPF_ID_MAP_SIZE; i++)",
        "if (env->explore_alu_limits)",
        "ctx_access = true;",
        "ctx_access = BPF_CLASS(insn->code) == BPF_STX;",
        "if (!ctx_access)",
    )
    for needle in required_verifier:
        if needle not in verifier:
            die(f"missing verifier invariant: {needle}")

    attach_head = syscall.find("static int bpf_prog_attach(const union bpf_attr *attr)")
    attach_tail = syscall.find("prog = bpf_prog_get_type(attr->attach_bpf_fd, ptype);", attach_head)
    if attach_head < 0 or attach_tail < 0:
        die("could not isolate bpf_prog_attach type switch")
    attach_switch = syscall[attach_head:attach_tail]
    for needle in (
        "ptype = BPF_PROG_TYPE_SK_MSG;",
        "ptype = BPF_PROG_TYPE_SK_SKB;",
        "ptype = BPF_PROG_TYPE_LIRC_MODE2;\n\t\tbreak;",
    ):
        if needle not in attach_switch:
            die(f"missing syscall invariant: {needle}")
    if "sock_map_get_from_fd(attr, prog)" in attach_switch:
        die("bpf_prog_attach still dereferences prog before initialization")

    header_path.write_text(header)
    verifier_path.write_text(verifier)
    syscall_path.write_text(syscall)

    changed = [str(p.relative_to(kernel)) for p in (header_path, verifier_path, syscall_path)
               if before[p] != p.read_text()]
    if not changed:
        die("no BPF changes were required; this baseline does not contain the expected bug")

    (report_dir / "report.txt").write_text(
        "A52 BPF Boot Reliability P1\n"
        "baseline=GitHub Actions 35505355071\n"
        "kernel_target=4.19.206\n"
        "\n" + "\n".join(f"{name}={state}" for name, state in status) +
        "\nchanged_files=" + ",".join(changed) + "\n"
    )

    print((report_dir / "report.txt").read_text(), end="")


if __name__ == "__main__":
    main()
