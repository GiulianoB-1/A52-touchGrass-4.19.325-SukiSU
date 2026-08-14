#!/usr/bin/env python3
"""Phase263 GKI 5.10 compatibility wrapper for the Golden PIL/SSR provider."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "263_a615_zap_pil_parity_base.py"
COMPAT_MARKER = "A52_PHASE263_PIL_GKI510_COMPAT_V1"
SSR_API_MARKER = "A52_PHASE263_SSR_GKI510_API_V2"


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

    pr_marker = f"/* {COMPAT_MARKER}: forced-include pr_fmt reset */"
    if pr_marker not in text:
        anchor = '#define pr_fmt(fmt) "subsys-restart: %s(): " fmt, __func__\n'
        if text.count(anchor) != 1:
            raise RuntimeError(
                f"Phase263 subsystem_restart pr_fmt anchor drifted: {text.count(anchor)}"
            )
        text = text.replace(anchor, pr_marker + "\n#undef pr_fmt\n" + anchor, 1)

    api_marker = f"/* {SSR_API_MARKER}: Android 4.19 SSR APIs adapted to GKI 5.10 */"
    if api_marker not in text:
        time_include = "#include <linux/time.h>\n"
        if text.count(time_include) != 1:
            raise RuntimeError(
                f"Phase263 subsystem_restart time include drifted: {text.count(time_include)}"
            )
        text = text.replace(
            time_include,
            time_include + "#include <linux/timekeeping.h>\n" + api_marker + "\n",
            1,
        )

        if text.count("struct timeval") != 2:
            raise RuntimeError(
                f"Phase263 subsystem_restart timeval shape drifted: {text.count('struct timeval')}"
            )
        text = text.replace("struct timeval", "struct timespec64")

        old_now = "do_gettimeofday(&r_log->time);"
        if text.count(old_now) != 1:
            raise RuntimeError(
                f"Phase263 subsystem_restart do_gettimeofday shape drifted: {text.count(old_now)}"
            )
        text = text.replace(old_now, "ktime_get_real_ts64(&r_log->time);", 1)

        old_match = "static int __find_subsys_device(struct device *dev, void *data)"
        new_match = "static int __find_subsys_device(struct device *dev, const void *data)"
        if text.count(old_match) != 1:
            raise RuntimeError(
                f"Phase263 subsystem_restart bus callback shape drifted: {text.count(old_match)}"
            )
        text = text.replace(old_match, new_match, 1)

    path.write_text(text, encoding="utf-8")


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
    for token in (
        "#undef pr_fmt",
        COMPAT_MARKER,
        SSR_API_MARKER,
        "#include <linux/timekeeping.h>",
        "struct timespec64 time;",
        "struct timespec64 *time_first = NULL, *curr_time;",
        "ktime_get_real_ts64(&r_log->time);",
        "static int __find_subsys_device(struct device *dev, const void *data)",
    ):
        if token not in restart:
            raise RuntimeError(f"Phase263 subsystem_restart 5.10 compatibility token missing: {token}")
    if "struct timeval" in restart or "do_gettimeofday" in restart:
        raise RuntimeError("Phase263 legacy timeval/do_gettimeofday API survived compatibility pass")
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
    assert SSR_API_MARKER.endswith("_V2")
    print("Phase 263 PIL/SSR GKI 5.10 compatibility self-test: PASS", flush=True)


def apply(root: Path) -> None:
    _base_apply(root)
    _patch_subsystem_restart(root)
    _stage_trace_header(root)
    _patch_ramdump(root)
    _verify_compat(root)
    print(f"{COMPAT_MARKER}: legacy PIL/SSR provider adapted to GKI 5.10", flush=True)
    print(f"{SSR_API_MARKER}: legacy SSR time/device APIs adapted to GKI 5.10", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(_base.locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
