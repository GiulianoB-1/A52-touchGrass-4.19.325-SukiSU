#!/usr/bin/env python3
"""Phase263 GKI 5.10 compatibility wrapper for the Golden PIL/SSR provider."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "263_a615_zap_pil_parity_base.py"
COMPAT_MARKER = "A52_PHASE263_PIL_GKI510_COMPAT_V1"


def _load_base():
    spec = importlib.util.spec_from_file_location("a52_phase263_pil_base", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Phase263 cannot load preserved base patcher: {BASE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_base = _load_base()
for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_base_apply = _base.apply
_base_self_test = _base.self_test


RAMDUMP_COMPAT = r'''/* A52_PHASE263_PIL_GKI510_COMPAT_V1 */
#ifndef DMA_ATTR_SKIP_ZEROING
#define DMA_ATTR_SKIP_ZEROING 0UL
#endif

static inline void *a52_dma_remap_compat(struct device *dev, void *cpu_addr,
		dma_addr_t dma_handle, size_t size, unsigned long attrs)
{
	(void)dev;
	(void)cpu_addr;
	(void)attrs;
	return memremap((resource_size_t)dma_handle, size, MEMREMAP_WB);
}

static inline void a52_dma_unremap_compat(struct device *dev,
		void *remapped_addr, size_t size)
{
	(void)dev;
	(void)size;
	memunmap(remapped_addr);
}

#define dma_remap a52_dma_remap_compat
#define dma_unremap a52_dma_unremap_compat

static inline char *a52_elf_str_table_compat(struct elfhdr *hdr)
{
	struct elf_shdr *shdr;

	if (hdr->e_shstrndx == SHN_UNDEF)
		return NULL;
	shdr = (struct elf_shdr *)((char *)hdr + hdr->e_shoff);
	return (char *)hdr + shdr[hdr->e_shstrndx].sh_offset;
}

#define elf_str_table a52_elf_str_table_compat
'''


def _patch_subsystem_restart(root: Path) -> None:
    path = root / "drivers/a52_pil/subsystem_restart.c"
    text = path.read_text(encoding="utf-8")
    marker = f"/* {COMPAT_MARKER}: forced-include pr_fmt reset */"
    if marker in text:
        return
    anchor = '#define pr_fmt(fmt) "subsys-restart: %s(): " fmt, __func__\n'
    if text.count(anchor) != 1:
        raise RuntimeError(f"Phase263 subsystem_restart pr_fmt anchor drifted: {text.count(anchor)}")
    replacement = marker + "\n#undef pr_fmt\n" + anchor
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")


def _stage_trace_header(root: Path) -> None:
    ws = root.parent.parent
    src = ws / "workspace/touchgrass-a52xq/include/trace/events/trace_msm_pil_event.h"
    dst = root / "include/trace/events/trace_msm_pil_event.h"
    if not src.is_file():
        raise RuntimeError(f"Phase263 Golden PIL trace header missing: {src}")
    text = src.read_text(encoding="utf-8")
    old = "#include <../drivers/soc/qcom/peripheral-loader.h>"
    new = "#include <../drivers/a52_pil/peripheral-loader.h>"
    if text.count(old) != 1:
        raise RuntimeError(f"Phase263 Golden PIL trace include drifted: {text.count(old)}")
    text = text.replace(old, new, 1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")


def _patch_ramdump(root: Path) -> None:
    path = root / "drivers/a52_pil/ramdump.c"
    text = path.read_text(encoding="utf-8")
    if COMPAT_MARKER in text:
        return
    anchor = "#include <linux/of.h>\n\n\n#define RAMDUMP_NUM_DEVICES"
    if text.count(anchor) != 1:
        raise RuntimeError(f"Phase263 ramdump compat anchor drifted: {text.count(anchor)}")
    replacement = "#include <linux/of.h>\n\n" + RAMDUMP_COMPAT + "\n#define RAMDUMP_NUM_DEVICES"
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")


def _verify_compat(root: Path) -> None:
    restart = (root / "drivers/a52_pil/subsystem_restart.c").read_text(encoding="utf-8")
    ramdump = (root / "drivers/a52_pil/ramdump.c").read_text(encoding="utf-8")
    trace = (root / "include/trace/events/trace_msm_pil_event.h").read_text(encoding="utf-8")
    if "#undef pr_fmt" not in restart or COMPAT_MARKER not in restart:
        raise RuntimeError("Phase263 pr_fmt compatibility guard missing")
    if "#include <../drivers/a52_pil/peripheral-loader.h>" not in trace:
        raise RuntimeError("Phase263 PIL trace header was not retargeted to staged provider")
    for token in (
        "#define DMA_ATTR_SKIP_ZEROING 0UL",
        "a52_dma_remap_compat",
        "memremap((resource_size_t)dma_handle, size, MEMREMAP_WB)",
        "a52_dma_unremap_compat",
        "a52_elf_str_table_compat",
        "#define elf_str_table a52_elf_str_table_compat",
    ):
        if token not in ramdump:
            raise RuntimeError(f"Phase263 ramdump 5.10 compatibility token missing: {token}")


def self_test() -> None:
    _base_self_test()
    assert "DMA_ATTR_SKIP_ZEROING 0UL" in RAMDUMP_COMPAT
    assert "memremap((resource_size_t)dma_handle, size, MEMREMAP_WB)" in RAMDUMP_COMPAT
    assert "memunmap(remapped_addr)" in RAMDUMP_COMPAT
    assert "a52_elf_str_table_compat" in RAMDUMP_COMPAT
    print("Phase 263 PIL/SSR GKI 5.10 compatibility self-test: PASS", flush=True)


def apply(root: Path) -> None:
    _base_apply(root)
    _patch_subsystem_restart(root)
    _stage_trace_header(root)
    _patch_ramdump(root)
    _verify_compat(root)
    print(f"{COMPAT_MARKER}: legacy PIL/SSR provider adapted to GKI 5.10", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(_base.locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
