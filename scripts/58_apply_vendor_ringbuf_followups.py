#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


class PatchError(RuntimeError):
    pass


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_verifier(path: Path) -> None:
    text = path.read_text()

    if "ringbuf PTR_TO_MEM spill support" not in text:
        text = replace_once(
            text,
            "\tcase PTR_TO_TCP_SOCK_OR_NULL:\n\t\treturn true;\n",
            "\tcase PTR_TO_TCP_SOCK_OR_NULL:\n"
            "\tcase PTR_TO_MEM:\n"
            "\tcase PTR_TO_MEM_OR_NULL:\n"
            "\t\t/* ringbuf PTR_TO_MEM spill support */\n"
            "\t\treturn true;\n",
            "PTR_TO_MEM spilling",
        )

    if "ringbuf helper-to-map compatibility" not in text:
        text = replace_once(
            text,
            "\t\t    func_id != BPF_FUNC_ringbuf_reserve &&\n"
            "\t\t    func_id != BPF_FUNC_ringbuf_submit &&\n"
            "\t\t    func_id != BPF_FUNC_ringbuf_discard &&\n"
            "\t\t    func_id != BPF_FUNC_ringbuf_query)\n",
            "\t\t    func_id != BPF_FUNC_ringbuf_reserve &&\n"
            "\t\t    func_id != BPF_FUNC_ringbuf_query)\n",
            "ringbuf map-side compatibility",
        )
        text = replace_once(
            text,
            "\tcase BPF_FUNC_get_stackid:\n",
            "\t/* ringbuf helper-to-map compatibility */\n"
            "\tcase BPF_FUNC_ringbuf_output:\n"
            "\tcase BPF_FUNC_ringbuf_reserve:\n"
            "\tcase BPF_FUNC_ringbuf_query:\n"
            "\t\tif (map->map_type != BPF_MAP_TYPE_RINGBUF)\n"
            "\t\t\tgoto error;\n"
            "\t\tbreak;\n"
            "\tcase BPF_FUNC_get_stackid:\n",
            "ringbuf helper-side compatibility",
        )

    if "reject ringbuf nullable pointer arithmetic" not in text:
        text = replace_once(
            text,
            "\tcase PTR_TO_MAP_VALUE_OR_NULL:\n"
            "\t\tverbose(env, \"R%d pointer arithmetic on %s prohibited, null-check it first\\n\",\n",
            "\tcase PTR_TO_MAP_VALUE_OR_NULL:\n"
            "\tcase PTR_TO_MEM_OR_NULL: /* reject ringbuf nullable pointer arithmetic */\n"
            "\t\tverbose(env, \"R%d pointer arithmetic on %s prohibited, null-check it first\\n\",\n",
            "nullable ringbuf pointer arithmetic",
        )

    path.write_text(text)


def patch_ringbuf(path: Path) -> None:
    text = path.read_text()

    if "!is_power_of_2(attr->max_entries)" not in text:
        text = replace_once(
            text,
            "\tif (attr->key_size || attr->value_size ||\n"
            "\t    attr->max_entries == 0 || !PAGE_ALIGNED(attr->max_entries))\n",
            "\tif (attr->key_size || attr->value_size ||\n"
            "\t    !is_power_of_2(attr->max_entries) ||\n"
            "\t    !PAGE_ALIGNED(attr->max_entries))\n",
            "power-of-two map sizing",
        )

    if "reject reservation larger than ringbuf" not in text:
        text = replace_once(
            text,
            "\tlen = round_up(size + BPF_RINGBUF_HDR_SZ, 8);\n"
            "\tcons_pos = smp_load_acquire(&rb->consumer_pos);\n",
            "\tlen = round_up(size + BPF_RINGBUF_HDR_SZ, 8);\n"
            "\tif (len > rb->mask + 1)\n"
            "\t\treturn NULL; /* reject reservation larger than ringbuf */\n"
            "\n"
            "\tcons_pos = smp_load_acquire(&rb->consumer_pos);\n",
            "reservation size bound",
        )

    path.write_text(text)


def validate(root: Path) -> None:
    verifier = (root / "kernel/bpf/verifier.c").read_text()
    ringbuf = (root / "kernel/bpf/ringbuf.c").read_text()

    required = [
        (verifier, "ringbuf PTR_TO_MEM spill support"),
        (verifier, "ringbuf helper-to-map compatibility"),
        (verifier, "reject ringbuf nullable pointer arithmetic"),
        (ringbuf, "!is_power_of_2(attr->max_entries)"),
        (ringbuf, "reject reservation larger than ringbuf"),
    ]
    for data, marker in required:
        if marker not in data:
            raise PatchError(f"missing semantic fix: {marker}")

    start = verifier.index("case BPF_MAP_TYPE_RINGBUF:")
    end = verifier.index("\t\tbreak;", start)
    map_block = verifier[start:end]
    if "ringbuf_submit" in map_block or "ringbuf_discard" in map_block:
        raise PatchError("submit/discard remain in map compatibility block")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} KERNEL_DIR", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    patch_verifier(root / "kernel/bpf/verifier.c")
    patch_ringbuf(root / "kernel/bpf/ringbuf.c")
    validate(root)
    print("vendor ring-buffer correctness and security follow-ups applied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
