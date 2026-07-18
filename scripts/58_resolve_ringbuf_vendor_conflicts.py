#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path


class ResolveError(RuntimeError):
    pass


def fail(msg: str) -> None:
    raise ResolveError(msg)


def block(s: str) -> str:
    s = textwrap.dedent(s)
    if s.startswith("\n"):
        s = s[1:]
    return s


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=flags)
    if count != 1:
        fail(f"{label}: expected one match, found {count}")
    return updated


def conflict_spans(text: str, path: str) -> list[tuple[int, int]]:
    lines = text.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<<"):
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines) and not lines[i].startswith("======="):
            i += 1
        if i == len(lines):
            fail(f"{path}: unterminated ours section")
        i += 1
        while i < len(lines) and not lines[i].startswith(">>>>>>>"):
            i += 1
        if i == len(lines):
            fail(f"{path}: unterminated theirs section")
        spans.append((start, i + 1))
        i += 1
    return spans


def resolve_conflicts(text: str, path: str, replacements: list[str]) -> str:
    lines = text.splitlines(keepends=True)
    spans = conflict_spans(text, path)
    if len(spans) != len(replacements):
        fail(f"{path}: expected {len(replacements)} conflicts, found {len(spans)}")
    out: list[str] = []
    cursor = 0
    for (start, end), replacement in zip(spans, replacements):
        out.extend(lines[cursor:start])
        out.append(replacement)
        cursor = end
    out.extend(lines[cursor:])
    result = "".join(out)
    if re.search(r"^(<<<<<<<|=======|>>>>>>>)", result, re.M):
        fail(f"{path}: conflict marker remains")
    return result


def resolve_linux_bpf_h(text: str) -> str:
    text = resolve_conflicts(text, "include/linux/bpf.h", [
        block("""
            int (*map_mmap)(struct bpf_map *map, struct vm_area_struct *vma);
            __poll_t (*map_poll)(struct bpf_map *map, struct file *filp,
                                 struct poll_table_struct *pts);
        """),
        block("""
            ARG_PTR_TO_ALLOC_MEM,       /* pointer to dynamically allocated memory */
            ARG_PTR_TO_ALLOC_MEM_OR_NULL, /* pointer to dynamically allocated memory or NULL */
            ARG_CONST_ALLOC_SIZE_OR_ZERO, /* number of allocated bytes requested */
        """),
        block("""
            PTR_TO_MEM,                 /* reg points to valid memory region */
            PTR_TO_MEM_OR_NULL,         /* reg points to valid memory region or NULL */
        """),
        block("""
            extern const struct bpf_func_proto bpf_ringbuf_output_proto;
            extern const struct bpf_func_proto bpf_ringbuf_reserve_proto;
            extern const struct bpf_func_proto bpf_ringbuf_submit_proto;
            extern const struct bpf_func_proto bpf_ringbuf_discard_proto;
            extern const struct bpf_func_proto bpf_ringbuf_query_proto;
        """),
    ])
    text = replace_once(
        text,
        "#include <linux/wait.h>\n",
        "#include <linux/wait.h>\n#include <linux/mm_types.h>\n#include <linux/poll.h>\n",
        "BPF mmap and poll includes",
    )
    return text


def resolve_linux_bpf_types_h(text: str) -> str:
    return resolve_conflicts(text, "include/linux/bpf_types.h", [
        "BPF_MAP_TYPE(BPF_MAP_TYPE_RINGBUF, ringbuf_map_ops)\n",
    ])


def resolve_linux_bpf_verifier_h(text: str) -> str:
    return resolve_conflicts(text, "include/linux/bpf_verifier.h", [
        "        u32 mem_size; /* for PTR_TO_MEM | PTR_TO_MEM_OR_NULL */\n\n",
    ])


def resolve_uapi_bpf_h(text: str) -> str:
    return resolve_conflicts(text, "include/uapi/linux/bpf.h", [
        block("""
            BPF_MAP_TYPE_REUSEPORT_SOCKARRAY,
            BPF_MAP_TYPE_PERCPU_CGROUP_STORAGE = 22,
            BPF_MAP_TYPE_DEVMAP_HASH = 25,
            BPF_MAP_TYPE_RINGBUF = 27,
        """),
        "",
        block("""
            FN(ktime_get_boot_ns),             \\
            FN(seq_printf),                    \\
            FN(seq_write),                     \\
            FN(sk_cgroup_id),                  \\
            FN(sk_ancestor_cgroup_id),         \\
            FN(ringbuf_output),                \\
            FN(ringbuf_reserve),               \\
            FN(ringbuf_submit),                \\
            FN(ringbuf_discard),               \\
            FN(ringbuf_query),
        """),
    ])


def resolve_makefile(text: str) -> str:
    return resolve_conflicts(text, "kernel/bpf/Makefile", [
        "obj-$(CONFIG_BPF_SYSCALL) += local_storage.o ringbuf.o\n",
    ])


def resolve_helpers(text: str) -> str:
    # The Samsung tree's common dispatcher is net/core/filter.c.
    return resolve_conflicts(text, "kernel/bpf/helpers.c", [""])


def resolve_syscall(text: str) -> str:
    return resolve_conflicts(text, "kernel/bpf/syscall.c", [
        block("""
            #include <linux/poll.h>

            #define IS_FD_ARRAY(map) ((map)->map_type == BPF_MAP_TYPE_PROG_ARRAY || \\
                                      (map)->map_type == BPF_MAP_TYPE_PERF_EVENT_ARRAY || \\
                                      (map)->map_type == BPF_MAP_TYPE_CGROUP_ARRAY || \\
                                      (map)->map_type == BPF_MAP_TYPE_ARRAY_OF_MAPS)
        """),
        block("""
            static int bpf_map_mmap(struct file *filp, struct vm_area_struct *vma)
            {
                struct bpf_map *map = filp->private_data;

                if (!map->ops->map_mmap || map_value_has_spin_lock(map))
                    return -ENOTSUPP;
                if (!(vma->vm_flags & VM_SHARED))
                    return -EINVAL;

                vma->vm_flags &= ~VM_MAYEXEC;
                return map->ops->map_mmap(map, vma);
            }

            static __poll_t bpf_map_poll(struct file *filp,
                                         struct poll_table_struct *pts)
            {
                struct bpf_map *map = filp->private_data;

                if (map->ops->map_poll)
                    return map->ops->map_poll(map, filp, pts);

                return EPOLLERR;
            }

        """),
        "    .mmap       = bpf_map_mmap,\n    .poll       = bpf_map_poll,\n",
    ])


def resolve_verifier(text: str) -> str:
    text = resolve_conflicts(text, "kernel/bpf/verifier.c", [
        block("""
            u64 msize_max_value;
            s64 msize_smax_value;
            u64 msize_umax_value;
            int mem_size;
            int ptr_id;
        """),
        block("""
                   type == PTR_TO_TCP_SOCK_OR_NULL ||
                   type == PTR_TO_MEM_OR_NULL;
            }

            static bool type_is_refcounted(enum bpf_reg_type type)
            {
                return type == PTR_TO_SOCKET || type == PTR_TO_MEM;
            }

            static bool type_is_refcounted_or_null(enum bpf_reg_type type)
            {
                return type == PTR_TO_SOCKET || type == PTR_TO_SOCKET_OR_NULL ||
                       type == PTR_TO_MEM || type == PTR_TO_MEM_OR_NULL;
            }

            static bool reg_is_refcounted(const struct bpf_reg_state *reg)
            {
                return type_is_refcounted(reg->type);
        """),
        "    return type_is_refcounted_or_null(reg->type);\n",
        block("""
                    func_id == BPF_FUNC_sk_lookup_udp ||
                    func_id == BPF_FUNC_ringbuf_reserve;
        """),
        block("""
            [PTR_TO_MEM]           = "mem",
            [PTR_TO_MEM_OR_NULL]   = "mem_or_null",
        """),
        block("""
            /* check read/write into memory region (e.g., map value, ringbuf sample, etc) */
            static int __check_mem_access(struct bpf_verifier_env *env, int regno,
                                          int off, int size, u32 mem_size,
                                          bool zero_size_allowed)
        """),
        block("""
                err = __check_mem_access(env, regno, reg->umax_value + off, size,
                                         mem_size, zero_size_allowed);
                if (err) {
                    verbose(env, "R%d max value is outside of the allowed memory range\\n",
                            regno);
                    return err;
                }

                return 0;
            }

            /* check read/write into a map element with possible variable offset */
            static int check_map_access(struct bpf_verifier_env *env, u32 regno,
                                        int off, int size, bool zero_size_allowed)
            {
                struct bpf_verifier_state *vstate = env->cur_state;
                struct bpf_func_state *state = vstate->frame[vstate->curframe];
                struct bpf_reg_state *reg = &state->regs[regno];
                struct bpf_map *map = reg->map_ptr;
                int err;

                err = check_mem_region_access(env, regno, off, size,
                                              map->value_size,
                                              zero_size_allowed);
                if (err)
                    return err;

                if (map_value_has_spin_lock(map)) {
                    u32 lock = map->spin_lock_off;

        """),
        "",
        block("""
            } else if (reg->type == PTR_TO_MEM) {
                if (t == BPF_WRITE && value_regno >= 0 &&
                    is_pointer_value(env, value_regno)) {
                    verbose(env, "R%d leaks addr into mem\\n", value_regno);
                    return -EACCES;
                }
                err = check_mem_region_access(env, regno, off, size,
                                              reg->mem_size, false);
                if (!err && t == BPF_READ && value_regno >= 0)
                    mark_reg_unknown(env, regs, value_regno);
        """),
        block("""
            } else if (arg_type_is_alloc_size(arg_type)) {
                if (!tnum_is_const(reg->var_off)) {
                    verbose(env, "R%d unbounded size, use 'var &= const' or 'if (var < const)'\\n",
                            regno);
                    return -EACCES;
                }
                meta->mem_size = reg->var_off.value;
        """),
        block("""
                } else if (reg->type == PTR_TO_MEM_OR_NULL) {
                    reg->type = PTR_TO_MEM;
        """),
    ])

    alloc_pattern = re.escape("} else if (arg_type_is_alloc_mem_ptr(arg_type)) {") + r".*?meta->ref_obj_id = reg->ref_obj_id;\n"
    alloc_replacement = block("""
        } else if (arg_type_is_alloc_mem_ptr(arg_type)) {
            expected_type = PTR_TO_MEM;
            if (register_is_null(reg) &&
                arg_type == ARG_PTR_TO_ALLOC_MEM_OR_NULL)
                /* final test in check_stack_boundary() */;
            else if (type != expected_type)
                goto err_type;
            if (meta->ptr_id || !reg->id) {
                verbose(env, "verifier internal error: mismatched ringbuf reference meta=%d, reg=%d\\n",
                        meta->ptr_id, reg->id);
                return -EFAULT;
            }
            meta->ptr_id = reg->id;
    """)
    text = regex_once(text, alloc_pattern, alloc_replacement,
                      "ringbuf argument reference metadata", re.S)

    ret_pattern = re.escape("} else if (fn->ret_type == RET_PTR_TO_ALLOC_MEM_OR_NULL) {") + r".*?regs\[BPF_REG_0\]\.mem_size = meta\.mem_size;\n"
    ret_replacement = block("""
        } else if (fn->ret_type == RET_PTR_TO_ALLOC_MEM_OR_NULL) {
            int id;

            mark_reg_known_zero(env, regs, BPF_REG_0);
            regs[BPF_REG_0].type = PTR_TO_MEM_OR_NULL;
            regs[BPF_REG_0].mem_size = meta.mem_size;
            id = acquire_reference_state(env, insn_idx);
            if (id < 0)
                return id;
            regs[BPF_REG_0].id = id;
    """)
    text = regex_once(text, ret_pattern, ret_replacement,
                      "ringbuf reservation reference acquisition", re.S)

    text = regex_once(
        text,
        r"} else if \(arg_type == ARG_PTR_TO_SOCKET\) \{\n"
        r"\s*expected_type = PTR_TO_SOCKET;\n"
        r"\s*if \(type != expected_type\)\n"
        r"\s*goto err_type;\n"
        r"(?=\s*} else if \(arg_type == ARG_PTR_TO_SPIN_LOCK\))",
        "",
        "duplicate vendor socket argument branch",
    )

    text = replace_once(
        text,
        "static bool arg_type_is_refcounted(enum bpf_arg_type type)\n{\n\treturn type == ARG_PTR_TO_SOCKET;\n}\n",
        "static bool arg_type_is_refcounted(enum bpf_arg_type type)\n{\n"
        "\treturn type == ARG_PTR_TO_SOCKET ||\n"
        "\t       type == ARG_PTR_TO_ALLOC_MEM;\n}\n",
        "ringbuf release argument refcounting",
    )

    text = regex_once(
        text,
        r"static bool is_acquire_function\(enum bpf_func_id func_id\)\n\{\n.*?\n\}",
        block("""
            static bool is_acquire_function(enum bpf_func_id func_id)
            {
                return func_id == BPF_FUNC_sk_lookup_tcp ||
                       func_id == BPF_FUNC_sk_lookup_udp ||
                       func_id == BPF_FUNC_ringbuf_reserve;
            }
        """).rstrip("\n"),
        "ringbuf acquire helper classification",
        re.S,
    )

    if "ref_obj_id" in text:
        # This old verifier does not use the newer ref_obj_id call metadata.
        fail("kernel/bpf/verifier.c: unexpected donor ref_obj_id remains")
    return text


def resolve_trace(text: str) -> str:
    return resolve_conflicts(text, "kernel/trace/bpf_trace.c", [
        block("""
            case BPF_FUNC_ringbuf_output:
                return &bpf_ringbuf_output_proto;
            case BPF_FUNC_ringbuf_reserve:
                return &bpf_ringbuf_reserve_proto;
            case BPF_FUNC_ringbuf_submit:
                return &bpf_ringbuf_submit_proto;
            case BPF_FUNC_ringbuf_discard:
                return &bpf_ringbuf_discard_proto;
            case BPF_FUNC_ringbuf_query:
                return &bpf_ringbuf_query_proto;
        """),
    ])


def adapt_ringbuf(text: str) -> str:
    text = regex_once(
        text,
        r"struct bpf_ringbuf_map \{\n\s*struct bpf_map map;\n\s*struct bpf_map_memory memory;\n\s*struct bpf_ringbuf \*rb;\n\};",
        "struct bpf_ringbuf_map {\n    struct bpf_map map;\n    struct bpf_ringbuf *rb;\n};",
        "vendor ringbuf map structure",
    )
    pattern = (
        r"[ \t]*cost = sizeof\(struct bpf_ringbuf_map\) \+\n"
        r"\s*sizeof\(struct bpf_ringbuf\) \+\n"
        r"\s*attr->max_entries;\n"
        r"\s*err = bpf_map_charge_init\(&rb_map->map\.memory, cost\);\n"
        r".*?"
        r"err_uncharge:\n\s*bpf_map_charge_finish\(&rb_map->map\.memory\);\n"
        r"err_free_map:\n"
    )
    replacement = block("""
            cost = sizeof(struct bpf_ringbuf_map) +
                   sizeof(struct bpf_ringbuf) +
                   attr->max_entries;
            /* This vendor tree charges map->pages after map_alloc() returns. */
            rb_map->map.pages = round_up(cost, PAGE_SIZE) >> PAGE_SHIFT;

            rb_map->rb = bpf_ringbuf_alloc(attr->max_entries,
                                           rb_map->map.numa_node);
            if (IS_ERR(rb_map->rb)) {
                err = PTR_ERR(rb_map->rb);
                goto err_free_map;
            }

            return &rb_map->map;

        err_free_map:
    """)
    return regex_once(text, pattern, replacement,
                      "vendor map memlock accounting", re.S)


def patch_filter(text: str) -> str:
    marker = "bpf_base_func_proto(enum bpf_func_id func_id)"
    start = text.find(marker)
    if start < 0:
        fail("net/core/filter.c: common helper dispatcher not found")
    end = text.find("\n}\n", start)
    if end < 0:
        fail("net/core/filter.c: common helper dispatcher end not found")
    section = text[start:end]
    if "BPF_FUNC_ringbuf_output" in section:
        return text
    pattern = (
        r"(?P<anchor>\s*case BPF_FUNC_ktime_get_boot_ns:\n"
        r"\s*return &bpf_ktime_get_boot_ns_proto;\n)"
    )
    cases = block("""
        case BPF_FUNC_ringbuf_output:
            return &bpf_ringbuf_output_proto;
        case BPF_FUNC_ringbuf_reserve:
            return &bpf_ringbuf_reserve_proto;
        case BPF_FUNC_ringbuf_submit:
            return &bpf_ringbuf_submit_proto;
        case BPF_FUNC_ringbuf_discard:
            return &bpf_ringbuf_discard_proto;
        case BPF_FUNC_ringbuf_query:
            return &bpf_ringbuf_query_proto;
    """)
    section, count = re.subn(pattern, lambda m: m.group("anchor") + cases,
                             section, count=1)
    if count != 1:
        fail(f"net/core/filter.c: dispatcher anchor count {count}")
    return text[:start] + section + text[end:]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} KERNEL_DIR", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    transforms = [
        ("include/linux/bpf.h", resolve_linux_bpf_h),
        ("include/linux/bpf_types.h", resolve_linux_bpf_types_h),
        ("include/linux/bpf_verifier.h", resolve_linux_bpf_verifier_h),
        ("include/uapi/linux/bpf.h", resolve_uapi_bpf_h),
        ("kernel/bpf/Makefile", resolve_makefile),
        ("kernel/bpf/helpers.c", resolve_helpers),
        ("kernel/bpf/syscall.c", resolve_syscall),
        ("kernel/bpf/verifier.c", resolve_verifier),
        ("kernel/trace/bpf_trace.c", resolve_trace),
        ("kernel/bpf/ringbuf.c", adapt_ringbuf),
        ("net/core/filter.c", patch_filter),
    ]
    for rel, transform in transforms:
        path = root / rel
        if not path.is_file():
            fail(f"missing {rel}")
        before = path.read_text()
        after = transform(before)
        path.write_text(after)
        print(f"resolved {rel}")

    invariants = {
        "include/uapi/linux/bpf.h": [
            "BPF_MAP_TYPE_RINGBUF = 27",
            "FN(ringbuf_query)",
            "BPF_RINGBUF_BUSY_BIT",
        ],
        "kernel/bpf/verifier.c": [
            "PTR_TO_MEM_OR_NULL",
            "meta->ptr_id = reg->id",
            "acquire_reference_state(env, insn_idx)",
        ],
        "kernel/bpf/ringbuf.c": [
            "const struct bpf_map_ops ringbuf_map_ops",
            "rb_map->map.pages = round_up",
        ],
        "net/core/filter.c": [
            "BPF_FUNC_ringbuf_output",
            "BPF_FUNC_ringbuf_query",
        ],
    }
    for rel, needles in invariants.items():
        data = (root / rel).read_text()
        for needle in needles:
            if needle not in data:
                fail(f"{rel}: missing invariant {needle}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResolveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
