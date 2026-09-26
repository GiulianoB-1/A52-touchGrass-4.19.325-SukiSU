#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE391_SECLOG_DUAL_BACKEND_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DWC3 = Path("drivers/usb/dwc3/core.c")

BLOCK = r'''
/* A52_PHASE391_SECLOG_DUAL_BACKEND_V1
 *
 * Samsung reserves a real 2 MiB sec_log buffer through:
 *     sec_log=0x200000@0xB1200000
 *
 * Phase389 guessed at a larger unaccounted region; debug.bin later proved that
 * region overlaps Samsung sec_debug ownership. Phase391 therefore uses ONLY
 * the explicit sec_log carveout. No UFS writes occur here.
 *
 * Existing 1 MiB ramoops/R48 remains unchanged. This second backend stores
 * fixed 128-byte CRC32C records and is recovered by the Phase390 recovery
 * snapshot exporter before OrangeFox/Samsung logging can reuse the RAM.
 *
 * 4 KiB header + 16,352 x 128-byte records = exactly 2 MiB.
 */
#define A52_P391_PHYS		0xB1200000ULL
#define A52_P391_BYTES		(2U * SZ_1M)
#define A52_P391_HEADER_BYTES	SZ_4K
#define A52_P391_RECORD_BYTES	128U
#define A52_P391_CAPACITY	((A52_P391_BYTES - A52_P391_HEADER_BYTES) / A52_P391_RECORD_BYTES)
#define A52_P391_MAGIC		0x313933474f4c4353ULL /* SCLOG391 */
#define A52_P391_REC_COMMIT	0x391c0de5U
#define A52_P391_VERSION	1U

struct a52_p391_header {
	u64 magic;
	u32 version;
	u32 record_bytes;
	u32 capacity;
	u32 reserved0;
	u64 boot_id;
	u64 write_seq;
	u64 write_seq_inv;
	u64 dropped;
	u64 dropped_inv;
};

struct a52_p391_record {
	u64 seq;
	u64 ts_ns;
	u16 cpu;
	u16 len;
	u32 reserved0;
	char text[96];
	u32 crc32c;
	u32 commit;
} __packed;

static const char a52_p391_marker[] __used =
	"A52_PHASE391_SECLOG_DUAL_BACKEND_V1";
static void __iomem *a52_p391_base;
static DEFINE_SPINLOCK(a52_p391_lock);
static u64 a52_p391_seq;
static u64 a52_p391_boot_id;
static atomic64_t a52_p391_dropped = ATOMIC64_INIT(0);

static u32 a52_p391_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				((crc & 1U) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

static void a52_p391_header_pair(size_t off, u64 value)
{
	u64 pair[2] = { value, ~value };

	memcpy_toio((u8 __iomem *)a52_p391_base + off, pair, sizeof(pair));
}

static void a52_p391_sec_write(const char *message)
{
	struct a52_p391_record rec;
	unsigned long flags;
	u64 slot;
	u64 dropped;
	size_t len;

	BUILD_BUG_ON(sizeof(struct a52_p391_record) != A52_P391_RECORD_BYTES);

	if (!READ_ONCE(a52_p391_base) || !message)
		return;

	if (!spin_trylock_irqsave(&a52_p391_lock, flags)) {
		atomic64_inc(&a52_p391_dropped);
		return;
	}

	memset(&rec, 0, sizeof(rec));
	rec.seq = ++a52_p391_seq;
	rec.ts_ns = ktime_get_boottime_ns();
	rec.cpu = (u16)raw_smp_processor_id();
	len = strnlen(message, sizeof(rec.text) - 1U);
	rec.len = (u16)len;
	memcpy(rec.text, message, len);
	rec.text[len] = '\0';
	rec.crc32c = a52_p391_crc32c(&rec,
				     offsetof(struct a52_p391_record, crc32c));
	rec.commit = A52_P391_REC_COMMIT;

	slot = (rec.seq - 1ULL) % A52_P391_CAPACITY;
	memcpy_toio((u8 __iomem *)a52_p391_base + A52_P391_HEADER_BYTES +
		    slot * A52_P391_RECORD_BYTES, &rec, sizeof(rec));

	a52_p391_header_pair(offsetof(struct a52_p391_header, write_seq),
			     a52_p391_seq);
	dropped = (u64)atomic64_read(&a52_p391_dropped);
	a52_p391_header_pair(offsetof(struct a52_p391_header, dropped),
			     dropped);
	spin_unlock_irqrestore(&a52_p391_lock, flags);
}

static int __init a52_p391_init(void)
{
	struct a52_p391_header old = { 0 };
	struct a52_p391_header h = { 0 };

	BUILD_BUG_ON(sizeof(struct a52_p391_header) > A52_P391_HEADER_BYTES);
	BUILD_BUG_ON(A52_P391_HEADER_BYTES +
		     A52_P391_CAPACITY * A52_P391_RECORD_BYTES !=
		     A52_P391_BYTES);

	a52_p391_base = ioremap_cache(A52_P391_PHYS, A52_P391_BYTES);
	if (!a52_p391_base) {
		a52_ackfr_record("P391 SECLOG map=0");
		return 0;
	}

	memcpy_fromio(&old, a52_p391_base, sizeof(old));
	if (old.magic == A52_P391_MAGIC &&
	    old.version == A52_P391_VERSION &&
	    old.boot_id && old.boot_id != ~0ULL)
		a52_p391_boot_id = old.boot_id + 1ULL;
	else
		a52_p391_boot_id = 1ULL;

	h.magic = A52_P391_MAGIC;
	h.version = A52_P391_VERSION;
	h.record_bytes = A52_P391_RECORD_BYTES;
	h.capacity = A52_P391_CAPACITY;
	h.boot_id = a52_p391_boot_id;
	h.write_seq_inv = ~0ULL;
	h.dropped_inv = ~0ULL;

	/* Only reset metadata. Old slots are ignored until write_seq reaches them. */
	memset_io(a52_p391_base, 0, A52_P391_HEADER_BYTES);
	memcpy_toio(a52_p391_base, &h, sizeof(h));
	wmb();

	a52_p391_seq = 0;
	atomic64_set(&a52_p391_dropped, 0);
	a52_ackfr_record("P391 SECLOG map=1 phys=%llx bytes=%x cap=%u boot=%llu",
		(unsigned long long)A52_P391_PHYS, A52_P391_BYTES,
		A52_P391_CAPACITY, (unsigned long long)a52_p391_boot_id);
	return 0;
}
core_initcall_sync(a52_p391_init);

'''


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase391 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit("Phase391 a52_ackfr_record signature missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit("Phase391 a52_ackfr_record opening brace missing")
    depth = 0
    for pos in range(brace, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit("Phase391 a52_ackfr_record closing brace missing")


def find_formatter(fn: str) -> tuple[int, str]:
    candidates = ("vscnprintf(", "vsnprintf(")
    hits = [(fn.find(token), token) for token in candidates if fn.find(token) >= 0]
    if not hits:
        raise SystemExit("Phase391 could not find recorder formatter")
    pos, token = min(hits)
    openp = pos + len(token) - 1

    # Capture the first argument while respecting nested parentheses.
    depth = 0
    comma = -1
    for i in range(openp + 1, len(fn)):
        if fn[i] == "(":
            depth += 1
        elif fn[i] == ")":
            if depth:
                depth -= 1
        elif fn[i] == "," and depth == 0:
            comma = i
            break
    if comma < 0:
        raise SystemExit("Phase391 formatter first argument parse failed")
    dst = fn[openp + 1:comma].strip()

    # Find the semicolon ending this formatter call.
    depth = 0
    end = -1
    for i in range(openp, len(fn)):
        if fn[i] == "(":
            depth += 1
        elif fn[i] == ")":
            depth -= 1
        elif fn[i] == ";" and depth == 0:
            end = i + 1
            break
    if end < 0:
        raise SystemExit("Phase391 formatter statement end missing")
    return end, dst


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    anchor = "#include <linux/kernel.h>\n"
    for inc in (
        "#include <linux/io.h>\n",
        "#include <linux/sizes.h>\n",
        "#include <linux/spinlock.h>\n",
        "#include <linux/ktime.h>\n",
    ):
        if inc not in text:
            text = one(text, anchor, anchor + inc, "recorder include")

    sig = "void a52_ackfr_record(const char *fmt, ...)"
    start, end = function_bounds(text, sig)
    text = text[:start] + BLOCK + text[start:]
    start, end = function_bounds(text, sig)
    fn = text[start:end]

    fmt_end, dst = find_formatter(fn)
    injection = (
        fn[:fmt_end] +
        "\n\t/* A52_PHASE391_SECLOG_MIRROR_CALL_V1 */\n"
        f"\ta52_p391_sec_write({dst});" +
        fn[fmt_end:]
    )
    text = text[:start] + injection + text[end:]
    return text


def patch_dwc3(text: str) -> str:
    if "A52_PHASE391_DWC3_CORE_SPLIT_V1" in text:
        return text
    if "A52_PHASE386_CORE_STAGE_V2" not in text:
        raise SystemExit("Phase391 requires Phase386 DWC3 source")

    # Split the current -EPROBE_DEFER inside dwc3_core_init without changing
    # behavior. 0x221=phy_setup, 0x222=ULPI raw return, 0x22e=ULPI timeout
    # before conversion, 0x223=core_get_phy.
    old = '''	ret = dwc3_phy_setup(dwc);
	if (ret)
		goto err0;

	if (!dwc->ulpi_ready) {
		ret = dwc3_core_ulpi_init(dwc);
		if (ret) {
			if (ret == -ETIMEDOUT) {
				dwc3_core_soft_reset(dwc);
				ret = -EPROBE_DEFER;
			}
			goto err0;
		}
		dwc->ulpi_ready = true;
	}

	if (!dwc->phys_ready) {
		ret = dwc3_core_get_phy(dwc);
		if (ret)
			goto err0a;
		dwc->phys_ready = true;
	}
'''
    new = '''	/* A52_PHASE391_DWC3_CORE_SPLIT_V1 */
	ret = dwc3_phy_setup(dwc);
	a52_ackfr_sticky385_ofsimple(0x221U, ret);
	if (ret)
		goto err0;

	if (!dwc->ulpi_ready) {
		ret = dwc3_core_ulpi_init(dwc);
		a52_ackfr_sticky385_ofsimple(0x222U, ret);
		if (ret) {
			if (ret == -ETIMEDOUT) {
				a52_ackfr_sticky385_ofsimple(0x22eU, ret);
				dwc3_core_soft_reset(dwc);
				ret = -EPROBE_DEFER;
			}
			goto err0;
		}
		dwc->ulpi_ready = true;
	}

	if (!dwc->phys_ready) {
		ret = dwc3_core_get_phy(dwc);
		a52_ackfr_sticky385_ofsimple(0x223U, ret);
		if (ret)
			goto err0a;
		dwc->phys_ready = true;
	}
'''
    return one(text, old, new, "DWC3 core split")


def validate(root: Path) -> None:
    r = (root / REC).read_text()
    d = (root / DWC3).read_text()
    for token in (
        MARK,
        "A52_P391_PHYS\t\t0xB1200000ULL",
        "A52_P391_BYTES\t\t(2U * SZ_1M)",
        "A52_P391_RECORD_BYTES\t128U",
        "A52_P391_CAPACITY",
        "ioremap_cache(A52_P391_PHYS, A52_P391_BYTES)",
        "A52_PHASE391_SECLOG_MIRROR_CALL_V1",
        "a52_p391_sec_write(",
        "P391 SECLOG map=1 phys=%llx bytes=%x cap=%u boot=%llu",
    ):
        if token not in r:
            raise SystemExit("Phase391 recorder token missing: " + token)

    if "0xB1400000ULL" in r and "A52_PHASE389_RESERVED_RAM_FORENSICS_V1" in r:
        raise SystemExit("Phase391 unsafe Phase389 7 MiB recorder is still present")

    for token in (
        "A52_PHASE391_DWC3_CORE_SPLIT_V1",
        "a52_ackfr_sticky385_ofsimple(0x221U, ret)",
        "a52_ackfr_sticky385_ofsimple(0x222U, ret)",
        "a52_ackfr_sticky385_ofsimple(0x22eU, ret)",
        "a52_ackfr_sticky385_ofsimple(0x223U, ret)",
    ):
        if token not in d:
            raise SystemExit("Phase391 DWC3 token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    for rel in (REC, DWC3):
        if not (root / rel).is_file():
            raise SystemExit("Phase391 source missing: " + str(rel))

    if not ns.check_only:
        p = root / REC
        p.write_text(patch_recorder(p.read_text()))
        p = root / DWC3
        p.write_text(patch_dwc3(p.read_text()))

    validate(root)
    print("Phase391 sec_log dual backend + DWC3 core split: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
