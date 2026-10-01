#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 160_enable_zram_zstd_writeback.py <kernel_tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"
zram = root / "drivers/block/zram/zram_drv.c"
zcomp = root / "drivers/block/zram/zcomp.c"
zkconfig = root / "drivers/block/zram/Kconfig"
crypto_kconfig = root / "crypto/Kconfig"

for p in (cfg, zram, zcomp, zkconfig, crypto_kconfig):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

text = cfg.read_text()

required = (
    "CONFIG_ZRAM=y",
    "CONFIG_ZRAM_WRITEBACK=y",
    "CONFIG_ZRAM_LRU_WRITEBACK=y",
    "CONFIG_ZSTD_COMPRESS=y",
    "CONFIG_ZSTD_DECOMPRESS=y",
)
for item in required:
    if item not in text:
        raise SystemExit(f"required baseline config missing: {item}")

if "CONFIG_CRYPTO_ZSTD=y" not in text:
    old = "# CONFIG_CRYPTO_ZSTD is not set"
    if text.count(old) != 1:
        raise SystemExit(f"CRYPTO_ZSTD anchor count: {text.count(old)}")
    text = text.replace(old, "CONFIG_CRYPTO_ZSTD=y", 1)

cfg.write_text(text)

zcomp_text = zcomp.read_text()
for needle in (
    '#if IS_ENABLED(CONFIG_CRYPTO_ZSTD)',
    '"zstd",',
    'alloc_percpu(struct zcomp_strm *)',
    'return *get_cpu_ptr(comp->stream);',
):
    if needle not in zcomp_text:
        raise SystemExit(f"zcomp capability missing: {needle}")

zram_text = zram.read_text()
for needle in (
    "backing_dev_store",
    "writeback_store",
    "writeback_limit_enable_store",
    "writeback_limit_store",
    "bd_stat_show",
):
    if needle not in zram_text:
        raise SystemExit(f"zram writeback capability missing: {needle}")

if "CONFIG_ZRAM_LRU_WRITEBACK" in zkconfig.read_text():
    for needle in ("zram_wbd", "init_lru_writeback"):
        if needle not in zram_text:
            raise SystemExit(f"zram LRU writeback capability missing: {needle}")

ck = crypto_kconfig.read_text()
for needle in (
    'config CRYPTO_ZSTD',
    'select ZSTD_COMPRESS',
    'select ZSTD_DECOMPRESS',
):
    if needle not in ck:
        raise SystemExit(f"crypto zstd dependency missing: {needle}")

final = cfg.read_text()
if "CONFIG_CRYPTO_ZSTD=y" not in final:
    raise SystemExit("CONFIG_CRYPTO_ZSTD was not enabled")

print("P160 ZRAM capability enablement: PASS")
print("  ZRAM writeback: existing + enabled")
print("  ZRAM LRU writeback: existing + enabled")
print("  Zstd crypto backend: enabled")
print("  ZRAM compression streams: per-CPU")
