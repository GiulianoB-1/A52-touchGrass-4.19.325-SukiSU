#!/usr/bin/env python3
from pathlib import Path
import sys

P = "P169"
MARK = "A52_P169_BPF_TETHERING_DIAG_V1"


def die(msg):
    raise SystemExit(f"{P}: {msg}")


def rd(root, rel):
    p = root / rel
    if not p.is_file():
        die(f"missing {rel}")
    return p.read_text()


def wr(root, rel, text):
    (root / rel).write_text(text)


def rep1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def span(text, sig):
    a = text.find(sig)
    if a < 0:
        die(f"missing function {sig}")
    b = text.find("{", a)
    if b < 0:
        die(f"missing opening brace {sig}")
    depth = 0
    for i in range(b, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return a, i + 1
    die(f"unterminated function {sig}")


def block(text, sig):
    a, b = span(text, sig)
    return text[a:b]


def replace_function(text, sig, new):
    a, b = span(text, sig)
    return text[:a] + new + text[b:]


def patch_config(root, out):
    rel = "arch/arm64/configs/a52xq_defconfig"
    s = rd(root, rel)
    required = [
        "CONFIG_BPF=y",
        "CONFIG_BPF_SYSCALL=y",
        "CONFIG_BPF_JIT=y",
        "CONFIG_BPF_JIT_ALWAYS_ON=y",
        "CONFIG_HAVE_EBPF_JIT=y",
        "CONFIG_CGROUP_BPF=y",
        "CONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS=y",
        "CONFIG_NET_ACT_POLICE=y",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        die("required BPF baseline options missing: " + ", ".join(missing))
    out.append("BPF_CONFIG=unchanged")


def patch_bpf_syscall(root, out):
    rel = "kernel/bpf/syscall.c"
    s = rd(root, rel)
    if MARK in s:
        out.append("BPF_SYSCALL_DIAG=present")
        return

    helper_anchor = "int sysctl_unprivileged_bpf_disabled __read_mostly;\n"
    helper = """int sysctl_unprivileged_bpf_disabled __read_mostly;

static bool a52_p169_bpf_loader_task(void)
{
	return !strcmp(current->comm, "netbpfload") ||
	       !strcmp(current->comm, "bpfloader");
}
"""
    if "static bool a52_p169_bpf_loader_task(void)" not in s:
        s = rep1(s, helper_anchor, helper, "BPF loader task helper")

    sig = "static int bpf_prog_load(union bpf_attr *attr, union bpf_attr __user *uattr)"
    fn = block(s, sig)

    old = """\terr = bpf_check(&prog, attr, uattr);\n\tif (err < 0)\n\t\tgoto free_used_maps;\n\n\tprog = bpf_prog_select_runtime(prog, &err);\n\tif (err < 0)\n\t\tgoto free_used_maps;\n"""
    new = """\terr = bpf_check(&prog, attr, uattr);\n\tif (err < 0) {\n\t\tif (a52_p169_bpf_loader_task())\n\t\t\tpr_err("A52_P169_BPF_FAIL stage=verifier err=%d type=%u attach=%u insns=%u flags=0x%x name=%.*s\\n",\n\t\t\t       err, type, attr->expected_attach_type, attr->insn_cnt,\n\t\t\t       attr->prog_flags, BPF_OBJ_NAME_LEN, attr->prog_name);\n\t\tgoto free_used_maps;\n\t}\n\n\tprog = bpf_prog_select_runtime(prog, &err);\n\tif (err < 0) {\n\t\tif (a52_p169_bpf_loader_task())\n\t\t\tpr_err("A52_P169_BPF_FAIL stage=runtime_jit err=%d type=%u attach=%u insns=%u jited=%u jited_len=%u name=%.*s\\n",\n\t\t\t       err, type, attr->expected_attach_type, attr->insn_cnt,\n\t\t\t       prog->jited, prog->jited_len, BPF_OBJ_NAME_LEN, attr->prog_name);\n\t\tgoto free_used_maps;\n\t}\n\n\tif (a52_p169_bpf_loader_task())\n\t\tpr_info("A52_P169_BPF_OK stage=runtime_jit type=%u attach=%u insns=%u jited=%u jited_len=%u name=%.*s\\n",\n\t\t\ttype, attr->expected_attach_type, attr->insn_cnt,\n\t\t\tprog->jited, prog->jited_len, BPF_OBJ_NAME_LEN, attr->prog_name);\n"""
    if old not in fn:
        die("bpf_prog_load verifier/runtime anchor changed")
    fn = fn.replace(old, new, 1)
    s = replace_function(s, sig, fn)

    sig = "SYSCALL_DEFINE3(bpf, int, cmd, union bpf_attr __user *, uattr, unsigned int, size)"
    fn = block(s, sig)
    old = "\n\treturn err;\n}"
    new = """\n\tif (err < 0 && a52_p169_bpf_loader_task()) {\n\t\tpr_err("A52_P169_BPF_SYSCALL_FAIL cmd=%d err=%d prog_type=%u attach=%u insns=%u map_type=%u key=%u value=%u max=%u map_flags=0x%x prog_flags=0x%x name=%.*s\\n",\n\t\t       cmd, err, attr.prog_type, attr.expected_attach_type, attr.insn_cnt,\n\t\t       attr.map_type, attr.key_size, attr.value_size, attr.max_entries,\n\t\t       attr.map_flags, attr.prog_flags, BPF_OBJ_NAME_LEN, attr.prog_name);\n\t}\n\n\treturn err;\n}"""
    if fn.count(old) != 1:
        die(f"bpf syscall return anchor count {fn.count(old)}")
    fn = fn.replace(old, new, 1)
    s = replace_function(s, sig, fn)

    include_anchor = "#include <linux/nospec.h>\n"
    s = rep1(s, include_anchor, include_anchor + f"\n/* {MARK} */\n", "BPF syscall marker")
    wr(root, rel, s)
    out.append("BPF_SYSCALL_DIAG=patched")


def patch_access_trace(root, out):
    rel = "fs/open.c"
    s = rd(root, rel)
    if MARK in s:
        out.append("BPF_ACCESS_DIAG=present")
        return

    inc = "#include <linux/compat.h>\n"
    s = rep1(s, inc, inc + "#include <linux/sched.h>\n", "open.c sched include")

    anchor = """#ifdef CONFIG_KSU
extern int ksu_handle_faccessat(int *dfd, const char __user **filename_user, int *mode,
			                    int *flags);
#endif
"""
    helper = anchor + """
/* A52_P169_BPF_TETHERING_DIAG_V1 */
static bool a52_p169_bpf_loader_task_open(void)
{
	return !strcmp(current->comm, "netbpfload") ||
	       !strcmp(current->comm, "bpfloader");
}

static void a52_p169_note_bpf_access(const char __user *filename, int mode, long ret)
{
	char path[192];
	long n;

	if (!a52_p169_bpf_loader_task_open() || !filename)
		return;

	n = strncpy_from_user(path, filename, sizeof(path) - 1);
	if (n <= 0)
		return;
	path[sizeof(path) - 1] = '\\0';

	if (strncmp(path, "/sys/fs/bpf/", 12))
		return;

	pr_info("A52_P169_BPF_ACCESS ret=%ld mode=0x%x path=%s\\n",
		ret, mode, path);
}
"""
    s = rep1(s, anchor, helper, "open.c BPF access helper")

    old = """SYSCALL_DEFINE3(faccessat, int, dfd, const char __user *, filename, int, mode)
{
#ifdef CONFIG_KSU
	ksu_handle_faccessat(&dfd, &filename, &mode, NULL);
#endif
	return do_faccessat(dfd, filename, mode);
}

SYSCALL_DEFINE2(access, const char __user *, filename, int, mode)
{
	return do_faccessat(AT_FDCWD, filename, mode);
}
"""
    new = """SYSCALL_DEFINE3(faccessat, int, dfd, const char __user *, filename, int, mode)
{
	long ret;
#ifdef CONFIG_KSU
	ksu_handle_faccessat(&dfd, &filename, &mode, NULL);
#endif
	ret = do_faccessat(dfd, filename, mode);
	a52_p169_note_bpf_access(filename, mode, ret);
	return ret;
}

SYSCALL_DEFINE2(access, const char __user *, filename, int, mode)
{
	long ret = do_faccessat(AT_FDCWD, filename, mode);

	a52_p169_note_bpf_access(filename, mode, ret);
	return ret;
}
"""
    s = rep1(s, old, new, "access syscall wrappers")
    wr(root, rel, s)
    out.append("BPF_ACCESS_DIAG=patched")


def patch_arm64_jit(root, out):
    rel = "arch/arm64/net/bpf_jit_comp.c"
    s = rd(root, rel)
    if MARK in s:
        out.append("ARM64_JIT_DIAG=present")
        return

    inc = '#include <linux/slab.h>\n'
    s = rep1(s, inc, inc + '#include <linux/sched.h>\n#include <linux/string.h>\n',
             "arm64 JIT diagnostic includes")

    sig = "static int build_body(struct jit_ctx *ctx)"
    fn = block(s, sig)
    old = """\t\tif (ret)\n\t\t\treturn ret;\n"""
    new = """\t\tif (ret) {\n\t\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\t\tpr_err("A52_P169_JIT_FAIL stage=build_body ret=%d insn=%d code=0x%x dst=%u src=%u off=%d imm=%d image=%u\\n",\n\t\t\t\t       ret, i, insn->code, insn->dst_reg, insn->src_reg,\n\t\t\t\t       insn->off, insn->imm, ctx->image != NULL);\n\t\t\treturn ret;\n\t\t}\n"""
    if fn.count(old) != 1:
        die(f"build_body return anchor count {fn.count(old)}")
    fn = fn.replace(old, new, 1)
    s = replace_function(s, sig, fn)

    sig = "struct bpf_prog *bpf_int_jit_compile(struct bpf_prog *prog)"
    fn = block(s, sig)

    replacements = [
        (
            """\tif (IS_ERR(tmp))\n\t\treturn orig_prog;\n""",
            """\tif (IS_ERR(tmp)) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=blind err=%ld len=%u\\n", PTR_ERR(tmp), prog->len);\n\t\treturn orig_prog;\n\t}\n""",
            "JIT blinding",
        ),
        (
            """\t\tif (!jit_data) {\n\t\t\tprog = orig_prog;\n\t\t\tgoto out;\n\t\t}\n""",
            """\t\tif (!jit_data) {\n\t\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\t\tpr_err("A52_P169_JIT_FAIL stage=jit_data_alloc len=%u\\n", prog->len);\n\t\t\tprog = orig_prog;\n\t\t\tgoto out;\n\t\t}\n""",
            "JIT data allocation",
        ),
        (
            """\tif (ctx.offset == NULL) {\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            """\tif (ctx.offset == NULL) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=offset_alloc len=%u\\n", prog->len);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            "JIT offset allocation",
        ),
        (
            """\tif (build_body(&ctx)) {\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            """\tif (build_body(&ctx)) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=pass1_body len=%u\\n", prog->len);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            "JIT pass1 body",
        ),
        (
            """\tif (build_prologue(&ctx, was_classic)) {\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            """\tif (build_prologue(&ctx, was_classic)) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=pass1_prologue len=%u\\n", prog->len);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            "JIT pass1 prologue",
        ),
        (
            """\tif (header == NULL) {\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            """\tif (header == NULL) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=binary_alloc image_size=%d len=%u\\n", image_size, prog->len);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            "JIT binary allocation",
        ),
        (
            """\tif (build_body(&ctx)) {\n\t\tbpf_jit_binary_free(header);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            """\tif (build_body(&ctx)) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=pass2_body len=%u\\n", prog->len);\n\t\tbpf_jit_binary_free(header);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            "JIT pass2 body",
        ),
        (
            """\tif (validate_code(&ctx)) {\n\t\tbpf_jit_binary_free(header);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            """\tif (validate_code(&ctx)) {\n\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\tpr_err("A52_P169_JIT_FAIL stage=validate len=%u image_size=%d\\n", prog->len, image_size);\n\t\tbpf_jit_binary_free(header);\n\t\tprog = orig_prog;\n\t\tgoto out_off;\n\t}\n""",
            "JIT validation",
        ),
    ]
    for old, new, label in replacements:
        if fn.count(old) != 1:
            die(f"{label}: expected 1 anchor, found {fn.count(old)}")
        fn = fn.replace(old, new, 1)

    old = """\t\tif (extra_pass && ctx.idx != jit_data->ctx.idx) {\n\t\t\tpr_err_once("multi-func JIT bug %d != %d\\n",\n\t\t\t\t    ctx.idx, jit_data->ctx.idx);\n"""
    new = """\t\tif (extra_pass && ctx.idx != jit_data->ctx.idx) {\n\t\t\tpr_err_once("multi-func JIT bug %d != %d\\n",\n\t\t\t\t    ctx.idx, jit_data->ctx.idx);\n\t\t\tif (a52_p169_bpf_loader_task_jit())\n\t\t\t\tpr_err("A52_P169_JIT_FAIL stage=multifunc_size current=%d expected=%d len=%u\\n",\n\t\t\t\t       ctx.idx, jit_data->ctx.idx, prog->len);\n"""
    if fn.count(old) != 1:
        die(f"JIT multifunc anchor count {fn.count(old)}")
    fn = fn.replace(old, new, 1)

    old = """\tprog->bpf_func = (void *)ctx.image;\n\tprog->jited = 1;\n\tprog->jited_len = image_size;\n"""
    new = """\tprog->bpf_func = (void *)ctx.image;\n\tprog->jited = 1;\n\tprog->jited_len = image_size;\n\tif (a52_p169_bpf_loader_task_jit())\n\t\tpr_info("A52_P169_JIT_OK len=%u image_size=%d is_func=%u extra_pass=%u\\n",\n\t\t\tprog->len, image_size, prog->is_func, extra_pass);\n"""
    if fn.count(old) != 1:
        die(f"JIT success anchor count {fn.count(old)}")
    fn = fn.replace(old, new, 1)

    s = replace_function(s, sig, fn)
    marker_anchor = '#define pr_fmt(fmt) "bpf_jit: " fmt\n'
    jit_helper = marker_anchor + f"""\n/* {MARK} */
static bool a52_p169_bpf_loader_task_jit(void)
{{
	return !strcmp(current->comm, "netbpfload") ||
	       !strcmp(current->comm, "bpfloader");
}}
"""
    s = rep1(s, marker_anchor, jit_helper, "ARM64 JIT marker/helper")
    wr(root, rel, s)
    out.append("ARM64_JIT_DIAG=patched")


def audit(root, out):
    cfg = rd(root, "arch/arm64/configs/a52xq_defconfig")
    syscall = rd(root, "kernel/bpf/syscall.c")
    jit = rd(root, "arch/arm64/net/bpf_jit_comp.c")
    open_c = rd(root, "fs/open.c")

    required_cfg = [
        "CONFIG_BPF=y", "CONFIG_BPF_SYSCALL=y", "CONFIG_BPF_JIT=y",
        "CONFIG_BPF_JIT_ALWAYS_ON=y", "CONFIG_HAVE_EBPF_JIT=y",
        "CONFIG_CGROUP_BPF=y", "CONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS=y",
        "CONFIG_NET_ACT_POLICE=y",
    ]
    for opt in required_cfg:
        if opt not in cfg:
            die(f"audit missing config {opt}")

    for needle in [
        MARK,
        "A52_P169_BPF_FAIL stage=verifier",
        "A52_P169_BPF_FAIL stage=runtime_jit",
        "A52_P169_BPF_SYSCALL_FAIL",
    ]:
        if needle not in syscall:
            die(f"audit missing syscall diagnostic: {needle}")

    for needle in [
        MARK,
        "A52_P169_JIT_FAIL stage=build_body",
        "A52_P169_JIT_FAIL stage=pass1_body",
        "A52_P169_JIT_FAIL stage=pass2_body",
        "A52_P169_JIT_FAIL stage=multifunc_size",
        "A52_P169_JIT_OK",
    ]:
        if needle not in jit:
            die(f"audit missing JIT diagnostic: {needle}")

    for needle in [MARK, "A52_P169_BPF_ACCESS"]:
        if needle not in open_c:
            die(f"audit missing access diagnostic: {needle}")

    out.append("AUDIT=PASS")


def main():
    if len(sys.argv) != 2:
        die(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        die("not a kernel tree")

    out = []
    patch_config(root, out)
    patch_bpf_syscall(root, out)
    patch_access_trace(root, out)
    patch_arm64_jit(root, out)
    audit(root, out)

    print("P169 tethering/BPF compatibility diagnostic: PASS")
    for x in out:
        print(x)
    print("baseline=P168_booted_but_restart_bpfloader_failed")
    print("runtime_scope=BPF_diagnostics_only")
    print("config_delta=none")\n    print("scheduler_delta=none")
    print("gpu_delta=none")
    print("fuse_delta=none")
    print("storage_delta=none")
    print("display_delta=none")


if __name__ == "__main__":
    main()
