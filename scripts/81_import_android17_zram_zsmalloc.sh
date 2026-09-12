#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
DONOR_REPO="https://github.com/notkernel-oss/not_samsung.sm8250-4.19.git"
DONOR_COMMIT="a265508933bbecb682e45e3b956d95bf4426d60b"
ANDROID_REF="android17-6.18-2026-09_r1"
DONOR_DIR="$ROOT/workspace/phase81-zram-donor"
ART="$ROOT/artifacts"

test -d "$KERNEL/.git"
mkdir -p "$ART"
rm -rf "$DONOR_DIR"
git init -q "$DONOR_DIR"
git -C "$DONOR_DIR" remote add origin "$DONOR_REPO"
git -C "$DONOR_DIR" fetch -q --depth=1 origin "$DONOR_COMMIT"
git -C "$DONOR_DIR" checkout -q --detach FETCH_HEAD

echo "Phase81 donor=$DONOR_COMMIT"
echo "Android reference=$ANDROID_REF"

rm -rf "$KERNEL/drivers/block/zram"
mkdir -p "$KERNEL/drivers/block/zram"
cp -a "$DONOR_DIR/drivers/block/zram/." "$KERNEL/drivers/block/zram/"
cp "$DONOR_DIR/mm/zsmalloc.c" "$KERNEL/mm/zsmalloc.c"
cp "$DONOR_DIR/include/linux/zsmalloc.h" "$KERNEL/include/linux/zsmalloc.h"

python3 - "$KERNEL" <<'PY'
from pathlib import Path
import sys
k = Path(sys.argv[1])
p = k / "include/linux/highmem.h"
s = p.read_text()
if "static inline void memcpy_from_page(" not in s:
    anchor = "#endif /* _LINUX_HIGHMEM_H */"
    if s.count(anchor) != 1:
        raise SystemExit("highmem end anchor not unique")
    compat = r'''
/*
 * Phase81 compatibility for modern zsmalloc object-copy helpers.
 * Upstream semantics expressed using the 4.19 kmap_atomic API.
 */
static inline void memcpy_from_page(char *to, struct page *page,
                                    size_t offset, size_t len)
{
        char *from = kmap_atomic(page);

        memcpy(to, from + offset, len);
        kunmap_atomic(from);
}

static inline void memcpy_to_page(struct page *page, size_t offset,
                                  const char *from, size_t len)
{
        char *to = kmap_atomic(page);

        memcpy(to + offset, from, len);
        kunmap_atomic(to);
}

static inline void memzero_page(struct page *page, size_t offset, size_t len)
{
        char *addr = kmap_atomic(page);

        memset(addr + offset, 0, len);
        kunmap_atomic(addr);
}

'''
    s = s.replace(anchor, compat + anchor)
    p.write_text(s)
PY

python3 - "$KERNEL" <<'PY'
from pathlib import Path
import sys
k = Path(sys.argv[1])
p = k / "mm/Kconfig"
s = p.read_text()
if "config ZSMALLOC_CHAIN_SIZE" not in s:
    pos = s.find("config ZSMALLOC_STAT\n")
    if pos < 0:
        raise SystemExit("ZSMALLOC_STAT marker missing")
    nxt = s.find("\nconfig ", pos + 1)
    if nxt < 0:
        raise SystemExit("next Kconfig marker missing")
    block = r'''
config ZSMALLOC_CHAIN_SIZE
        int "Maximum number of physical pages per-zspage"
        default 4
        range 4 16
        depends on ZSMALLOC
        help
          Sets the upper limit on physical pages in a zspage. The allocator
          calculates the optimal chain length per size class.

'''
    s = s[:nxt+1] + block + s[nxt+1:]
    p.write_text(s)
PY

python3 - "$KERNEL" <<'PY'
from pathlib import Path
import sys
k = Path(sys.argv[1])
p = k / "arch/arm64/configs/a52xq_defconfig"
lines = p.read_text().splitlines()

set_y = {
    "LRU_GEN",
    "LRU_GEN_ENABLED",
    "RCU_NOCB_CPU",
    "RCU_FAST_NO_HZ",
    "ZSMALLOC",
    "ZRAM",
    "ZRAM_WRITEBACK",
    "ZRAM_BACKEND_LZO",
    "ZRAM_DEF_COMP_LZORLE",
    "LZO_COMPRESS",
    "LZO_DECOMPRESS",
}
set_n = {
    "LRU_GEN_STATS",
    "ZSMALLOC_STAT",
    "ZPOOL",
    "ZRAM_MEMORY_TRACKING",
    "ZRAM_MULTI_COMP",
    "ZRAM_DEDUP",
    "ZRAM_LRU_WRITEBACK",
}
set_values = {"ZSMALLOC_CHAIN_SIZE": "4"}

symbols = set_y | set_n | set(set_values) | {"ZRAM_LRU_WRITEBACK_LIMIT"}
out = []
for line in lines:
    key = None
    if line.startswith("CONFIG_") and "=" in line:
        key = line[7:].split("=", 1)[0]
    elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
        key = line[len("# CONFIG_"):-len(" is not set")]
    if key in symbols:
        continue
    out.append(line)

for key in sorted(set_y):
    out.append(f"CONFIG_{key}=y")
for key in sorted(set_n):
    out.append(f"# CONFIG_{key} is not set")
for key, val in sorted(set_values.items()):
    out.append(f"CONFIG_{key}={val}")

p.write_text("\n".join(out) + "\n")
PY

test ! -e "$KERNEL/drivers/block/zram/zram_dedup.c"
grep -Fq 'ZRAM_ENTRY_LOCK' "$KERNEL/drivers/block/zram/zram_drv.h"
grep -Fq 'wait_on_bit_lock' "$KERNEL/drivers/block/zram/zram_drv.c"
grep -Fq 'zs_obj_read_begin' "$KERNEL/drivers/block/zram/zram_drv.c"
grep -Fq 'zs_obj_write' "$KERNEL/drivers/block/zram/zram_drv.c"
grep -Fq 'zspage_read_lock' "$KERNEL/mm/zsmalloc.c"
grep -Fq 'ZS_OBJ_CLASS_BITS' "$KERNEL/mm/zsmalloc.c"
grep -Fq '__free_zspage_lockless' "$KERNEL/mm/zsmalloc.c"
grep -Fq 'CONFIG_LRU_GEN_ENABLED=y' "$KERNEL/arch/arm64/configs/a52xq_defconfig"
grep -Fq 'CONFIG_ZSMALLOC_CHAIN_SIZE=4' "$KERNEL/arch/arm64/configs/a52xq_defconfig"
grep -Fq 'CONFIG_ZRAM_BACKEND_LZO=y' "$KERNEL/arch/arm64/configs/a52xq_defconfig"

cat > "$ART/phase81-zram-zsmalloc-source.txt" <<EOF
phase=81
android_reference=$ANDROID_REF
android_reference_commit=bab5f6aca819542b9dd13a70d3c62271e81b8e85
compatibility_donor=notkernel-oss/not_samsung.sm8250-4.19
compatibility_donor_commit=$DONOR_COMMIT
zram_model=modern-upstream-style
zram_entry_lock=sleepable-bit-lock
zram_compression_stream=preemptible
zsmalloc_object_api=modern-read-write
zsmalloc_chain_size=4
zsmalloc_2026_lock_scalability=present
samsung_zram_dedup=replaced
samsung_zram_lru_writeback=replaced-by-upstream-writeback
mglru_default=enabled
rcu_nocb_policy=boot-parameter-cpus-6-7
rcu_lazy=not-used
EOF

git -C "$KERNEL" diff --check
echo "Phase81 Android17 ZRAM/zsmalloc import complete"
