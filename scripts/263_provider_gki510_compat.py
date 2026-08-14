#!/usr/bin/env python3
"""Phase263 provider compatibility shim for Android GKI 5.10.

This runs after the pinned TouchGrass 4.19 PIL/SSR closure has been staged into
``drivers/a52_pil``.  Every edit is anchored to an exact 4.19 construct and
fails closed if the generated tree drifts.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE263_PROVIDER_GKI510_COMPAT_V1"

PR_FMT_OLD = '#define pr_fmt(fmt) "subsys-restart: %s(): " fmt, __func__'
PR_FMT_NEW = '''#ifdef pr_fmt
#undef pr_fmt
#endif
#define pr_fmt(fmt) "subsys-restart: %s(): " fmt, __func__'''

TRACE_OLD = '#include <../drivers/soc/qcom/peripheral-loader.h>'
TRACE_NEW = '#include <peripheral-loader.h>'

MAKEFILE_INCLUDE_ANCHOR = 'ccflags-y += -I$(srctree)/a52-compat/include/uapi\n'
MAKEFILE_PROVIDER_INCLUDE = 'ccflags-y += -I$(srctree)/drivers/a52_pil\n'

RAMDUMP_DMA_OLD = '''\trd_dev->attrs = 0;
\trd_dev->attrs |= DMA_ATTR_SKIP_ZEROING;
\tdevice_mem = vaddr ?: dma_remap(rd_dev->dev->parent, NULL, addr,
\t\t\t\t\t\tcopy_size, rd_dev->attrs);'''
RAMDUMP_DMA_NEW = '''\t/* Android 5.10 removed dma_ops->remap/unremap.  This path only
\t * needs a CPU mapping of the physical ramdump segment, so use the
\t * generic physical-memory remapper and preserve the original WB view.
\t */
\tdevice_mem = vaddr ?: memremap(addr, copy_size, MEMREMAP_WB);'''

RAMDUMP_UNMAP_OLD = 'dma_unremap(rd_dev->dev->parent, origdevice_mem, copy_size);'
RAMDUMP_UNMAP_NEW = 'memunmap(origdevice_mem);'

ELF_CALL_OLD = '\tchar *strtab = elf_str_table(ehdr);'
ELF_CALL_NEW = '\tchar *strtab = a52_ramdump_elf_str_table(ehdr);'
ELF_ANCHOR = 'static inline unsigned int set_section_name(const char *name,'
ELF_HELPER = '''static inline char *a52_ramdump_elf_str_table(struct elfhdr *hdr)
{
\tstruct elf_shdr *sheaders;

\tif (hdr->e_shstrndx == SHN_UNDEF)
\t\treturn NULL;

\tsheaders = (struct elf_shdr *)((size_t)hdr + (size_t)hdr->e_shoff);
\treturn (char *)hdr + sheaders[hdr->e_shstrndx].sh_offset;
}

'''


def exact_replace(text: str, old: str, new: str, *, count: int, label: str) -> str:
    found = text.count(old)
    if found == 0 and new in text:
        return text
    if found != count:
        raise RuntimeError(f"Phase263 {label} anchor drifted: expected {count}, found {found}")
    return text.replace(old, new, count)


def patch_subsystem_restart(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = exact_replace(text, PR_FMT_OLD, PR_FMT_NEW, count=1, label="pr_fmt")
    if text.count(PR_FMT_NEW) != 1:
        raise RuntimeError("Phase263 pr_fmt compatibility contract missing/duplicated")
    path.write_text(text, encoding="utf-8")


def patch_trace_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = exact_replace(text, TRACE_OLD, TRACE_NEW, count=1, label="trace PIL header")
    if text.count(TRACE_NEW) != 1:
        raise RuntimeError("Phase263 trace provider include missing/duplicated")
    path.write_text(text, encoding="utf-8")


def patch_makefile(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MAKEFILE_PROVIDER_INCLUDE not in text:
        if text.count(MAKEFILE_INCLUDE_ANCHOR) != 1:
            raise RuntimeError("Phase263 provider Makefile include anchor drifted")
        text = text.replace(
            MAKEFILE_INCLUDE_ANCHOR,
            MAKEFILE_INCLUDE_ANCHOR + MAKEFILE_PROVIDER_INCLUDE,
            1,
        )
    if text.count(MAKEFILE_PROVIDER_INCLUDE) != 1:
        raise RuntimeError("Phase263 provider include path missing/duplicated")
    path.write_text(text, encoding="utf-8")


def patch_ramdump(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = exact_replace(
        text,
        RAMDUMP_DMA_OLD,
        RAMDUMP_DMA_NEW,
        count=1,
        label="ramdump dma_remap",
    )
    text = exact_replace(
        text,
        RAMDUMP_UNMAP_OLD,
        RAMDUMP_UNMAP_NEW,
        count=2,
        label="ramdump dma_unremap",
    )

    if ELF_HELPER not in text:
        if text.count(ELF_ANCHOR) != 1:
            raise RuntimeError("Phase263 ramdump ELF helper anchor drifted")
        text = text.replace(ELF_ANCHOR, ELF_HELPER + ELF_ANCHOR, 1)
    text = exact_replace(
        text,
        ELF_CALL_OLD,
        ELF_CALL_NEW,
        count=1,
        label="ramdump elf_str_table",
    )

    forbidden = (
        "DMA_ATTR_SKIP_ZEROING",
        "dma_remap(rd_dev->dev->parent",
        "dma_unremap(rd_dev->dev->parent",
        "char *strtab = elf_str_table(ehdr);",
    )
    for token in forbidden:
        if token in text:
            raise RuntimeError(f"Phase263 GKI 5.10 ramdump incompatibility survived: {token}")
    if text.count("memremap(addr, copy_size, MEMREMAP_WB)") != 1:
        raise RuntimeError("Phase263 ramdump memremap contract missing/duplicated")
    if text.count("memunmap(origdevice_mem);") != 2:
        raise RuntimeError("Phase263 ramdump memunmap contract missing/duplicated")
    if text.count("a52_ramdump_elf_str_table(ehdr)") != 1:
        raise RuntimeError("Phase263 ramdump ELF compatibility call missing/duplicated")

    path.write_text(text, encoding="utf-8")


def apply(root: Path) -> None:
    root = root.resolve()
    provider = root / "drivers/a52_pil"
    paths = {
        "subsystem_restart": provider / "subsystem_restart.c",
        "ramdump": provider / "ramdump.c",
        "makefile": provider / "Makefile",
        "trace": root / "a52-compat/include/trace/events/trace_msm_pil_event.h",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise RuntimeError("Phase263 GKI 5.10 compatibility inputs missing: " + ", ".join(missing))

    patch_subsystem_restart(paths["subsystem_restart"])
    patch_ramdump(paths["ramdump"])
    patch_trace_header(paths["trace"])
    patch_makefile(paths["makefile"])
    print(f"{MARKER}: provider compatibility applied", flush=True)


def self_test() -> None:
    sample = PR_FMT_OLD + "\n"
    assert PR_FMT_NEW in exact_replace(sample, PR_FMT_OLD, PR_FMT_NEW, count=1, label="selftest")

    dma = RAMDUMP_DMA_OLD + "\n" + RAMDUMP_UNMAP_OLD + "\n" + RAMDUMP_UNMAP_OLD
    dma = exact_replace(dma, RAMDUMP_DMA_OLD, RAMDUMP_DMA_NEW, count=1, label="selftest dma")
    dma = exact_replace(dma, RAMDUMP_UNMAP_OLD, RAMDUMP_UNMAP_NEW, count=2, label="selftest unmap")
    assert "DMA_ATTR_SKIP_ZEROING" not in dma
    assert dma.count("memunmap(origdevice_mem);") == 2
    print("Phase 263 GKI 5.10 provider compatibility self-test: PASS", flush=True)


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    root = Path(argv[0]) if argv else Path("gki/common")
    apply(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
