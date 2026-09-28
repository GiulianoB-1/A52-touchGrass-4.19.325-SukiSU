#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE406_P392_CACHE_PERSIST_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase406 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE405_DMA_DONE_DIRECT_V1" not in text:
        raise SystemExit("Phase406 requires Phase405 direct DMA recorder")
    if "#include <asm/cacheflush.h>\n" not in text:
        raise SystemExit("Phase406 requires cacheflush support")

    helper_anchor = "static atomic_t a52_p405_seq = ATOMIC_INIT(0);\n"
    helper = r'''
static void a52_p406_persist(void __iomem *addr, size_t len)
{
	if (!addr || !len)
		return;
	wmb();
	__flush_dcache_area((void __force *)addr, len);
	wmb();
}

'''
    text = one(text, helper_anchor, helper_anchor + helper, "persist helper")

    old = '''	memcpy_toio((u8 __iomem *)base + A52_P405_OFF0 +
		    (unsigned int)stage * A52_P405_SLOT_BYTES,
		    &slot, sizeof(slot));
	memcpy_toio((u8 __iomem *)base + A52_P405_OFF1 +
		    (unsigned int)stage * A52_P405_SLOT_BYTES,
		    &slot, sizeof(slot));
	wmb();
'''
    new = '''	{
		void __iomem *dst0 = (u8 __iomem *)base + A52_P405_OFF0 +
			(unsigned int)stage * A52_P405_SLOT_BYTES;
		void __iomem *dst1 = (u8 __iomem *)base + A52_P405_OFF1 +
			(unsigned int)stage * A52_P405_SLOT_BYTES;

		memcpy_toio(dst0, &slot, sizeof(slot));
		memcpy_toio(dst1, &slot, sizeof(slot));
		a52_p406_persist(dst0, sizeof(slot));
		a52_p406_persist(dst1, sizeof(slot));
	}
'''
    text = one(text, old, new, "Phase405 fixed-slot flush")

    old = '''static void a52_p392_header_pair(size_t off, u64 value)
{
	u64 pair[2] = { value, ~value };

	memcpy_toio((u8 __iomem *)a52_p392_base + off, pair, sizeof(pair));
}
'''
    new = '''static void a52_p392_header_pair(size_t off, u64 value)
{
	u64 pair[2] = { value, ~value };
	void __iomem *dst = (u8 __iomem *)a52_p392_base + off;

	memcpy_toio(dst, pair, sizeof(pair));
	a52_p406_persist(dst, sizeof(pair));
}
'''
    text = one(text, old, new, "Phase392 header-pair flush")

    old = '''	slot = (rec.seq - 1ULL) % A52_P392_CAPACITY;
	for (copy = 0; copy < A52_P392_COPIES; copy++)
		memcpy_toio((u8 __iomem *)a52_p392_base + A52_P392_HEADER_BYTES +
			    copy * A52_P392_COPY_BYTES +
			    slot * A52_P392_RECORD_BYTES, &rec, sizeof(rec));

	a52_p392_header_pair(offsetof(struct a52_p392_header, write_seq),
'''
    new = '''	slot = (rec.seq - 1ULL) % A52_P392_CAPACITY;
	for (copy = 0; copy < A52_P392_COPIES; copy++) {
		void __iomem *dst =
			(u8 __iomem *)a52_p392_base + A52_P392_HEADER_BYTES +
			copy * A52_P392_COPY_BYTES +
			slot * A52_P392_RECORD_BYTES;

		memcpy_toio(dst, &rec, sizeof(rec));
		a52_p406_persist(dst, sizeof(rec));
	}

	a52_p392_header_pair(offsetof(struct a52_p392_header, write_seq),
'''
    text = one(text, old, new, "Phase392 record-copy flush")

    old = '''	memset_io(a52_p392_base, 0, A52_P392_HEADER_BYTES);
	memcpy_toio(a52_p392_base, &h, sizeof(h));
	wmb();

	a52_p392_seq = 0;
'''
    new = '''	memset_io(a52_p392_base, 0, A52_P392_HEADER_BYTES);
	memcpy_toio(a52_p392_base, &h, sizeof(h));
	a52_p406_persist(a52_p392_base, A52_P392_HEADER_BYTES);

	a52_p392_seq = 0;
'''
    text = one(text, old, new, "Phase392 init-header flush")

    text += (
        "\n/* " + MARK + "\n"
        " * Phase392 uses ioremap_cache(); explicit dcache cleaning is required\n"
        " * for reserved-RAM evidence to survive reset into recovery.\n"
        " */\n"
        "static const char a52_p406_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "static void a52_p406_persist(void __iomem *addr, size_t len)",
        "__flush_dcache_area((void __force *)addr, len);",
        "a52_p406_persist(dst0, sizeof(slot));",
        "a52_p406_persist(dst1, sizeof(slot));",
        "a52_p406_persist(dst, sizeof(pair));",
        "a52_p406_persist(dst, sizeof(rec));",
        "a52_p406_persist(a52_p392_base, A52_P392_HEADER_BYTES);",
        "ioremap_cache(A52_P392_PHYS, A52_P392_BYTES)",
    ):
        if token not in text:
            raise SystemExit("Phase406 token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    p = ns.root / REC
    if not p.is_file():
        raise SystemExit("Phase406 recorder source missing")

    text = p.read_text(errors="replace")
    if not ns.check_only:
        text = patch(text)
        p.write_text(text)
    validate(p.read_text(errors="replace"))
    print("Phase406 Phase392/405 cache persistence: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
