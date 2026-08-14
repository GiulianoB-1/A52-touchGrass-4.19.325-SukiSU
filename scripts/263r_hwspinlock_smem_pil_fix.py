#!/usr/bin/env python3
"""Phase263R: repair the Phase263 Qualcomm HWSPINLOCK -> SMEM -> PIL dependency chain.

Golden FDR comparison showed that Phase263 reached device initcalls with qcom-smem
still deferred because CONFIG_HWSPINLOCK_QCOM was missing. The imported Golden PIL
code then had an unsafe error-pointer lifetime for g_md_toc. This corrective phase:

1. enables CONFIG_HWSPINLOCK_QCOM,
2. restores Golden-relative PIL-TZ-before-microdump device-init ordering, and
3. prevents qcom_smem_get() errors from leaving g_md_toc as an ERR_PTR.

This is a GKI 5.10 compatibility correction on top of Phase263, not a new PIL design.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE263R_HWSPINLOCK_SMEM_PIL_FIX_V1"
ORDER_MARKER = "A52_PHASE263R_GOLDEN_PIL_INIT_ORDER_V1"
MINIDUMP_MARKER = "A52_PHASE263R_MINIDUMP_ERRPTR_GUARD_V1"
CONFIG_LINE = "CONFIG_HWSPINLOCK_QCOM=y"

PHASE263_OBJECT_LINE = (
    "obj-y += subsystem_notif.o subsystem_restart.o ramdump.o "
    "microdump_collector.o peripheral-loader.o subsys-pil-tz.o"
)
GOLDEN_RELATIVE_OBJECT_LINE = (
    "obj-y += subsys-pil-tz.o peripheral-loader.o subsystem_notif.o "
    "subsystem_restart.o ramdump.o microdump_collector.o"
)

OLD_TOC_GUARD = "if (g_md_toc && g_md_toc->md_toc_init == true) {"
NEW_TOC_GUARD = (
    "if (!IS_ERR_OR_NULL(g_md_toc) && g_md_toc->md_toc_init == true) {"
)

OLD_SMEM_BLOCK = '''\t/* Get Global minidump ToC*/
\tg_md_toc = qcom_smem_get(QCOM_SMEM_HOST_ANY, SBL_MINIDUMP_SMEM_ID,
\t\t\t\t &size);
\tpr_debug("Minidump: g_md_toc is %pa\\n", &g_md_toc);
\tif (PTR_ERR(g_md_toc) == -EPROBE_DEFER) {
\t\tpr_err("SMEM is not initialized.\\n");
\t\treturn -EPROBE_DEFER;
\t}
'''

NEW_SMEM_BLOCK = f'''\t/* {MINIDUMP_MARKER}: never retain an ERR_PTR in g_md_toc. */
\t/* Get Global minidump ToC. Minidump is optional for PIL registration. */
\tg_md_toc = qcom_smem_get(QCOM_SMEM_HOST_ANY, SBL_MINIDUMP_SMEM_ID,
\t\t\t\t &size);
\tpr_debug("Minidump: g_md_toc is %pa\\n", &g_md_toc);
\tif (IS_ERR(g_md_toc)) {{
\t\tret = PTR_ERR(g_md_toc);
\t\tg_md_toc = NULL;
\t\tpr_warn("Minidump: SMEM ToC unavailable rc=%d; continuing without minidump\\n",
\t\t\tret);
\t}}
'''


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        return Path(argv[0]).resolve()
    return Path("gki/common").resolve()


def workspace(root: Path) -> Path:
    return root.parent.parent


def set_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    disabled = "# CONFIG_HWSPINLOCK_QCOM is not set"
    if CONFIG_LINE in text.splitlines():
        return
    if disabled in text:
        text = text.replace(disabled, CONFIG_LINE, 1)
    else:
        text = text.rstrip() + "\n" + CONFIG_LINE + "\n"
    path.write_text(text, encoding="utf-8")


def patch_object_order(root: Path) -> None:
    path = root / "drivers/a52_pil/Makefile"
    text = path.read_text(encoding="utf-8")
    if ORDER_MARKER in text:
        return
    if text.count(PHASE263_OBJECT_LINE) != 1:
        raise RuntimeError(
            "Phase263R expected exactly one Phase263 PIL object-order line, found "
            f"{text.count(PHASE263_OBJECT_LINE)}"
        )
    replacement = (
        f"# {ORDER_MARKER}: mirror Golden relative device-init ordering\n"
        + GOLDEN_RELATIVE_OBJECT_LINE
    )
    path.write_text(text.replace(PHASE263_OBJECT_LINE, replacement, 1), encoding="utf-8")


def patch_minidump_error_lifetime(root: Path) -> None:
    path = root / "drivers/a52_pil/peripheral-loader.c"
    text = path.read_text(encoding="utf-8")

    if MINIDUMP_MARKER not in text:
        decl = "\tint i;\n\tsize_t size;"
        if text.count(decl) != 1:
            raise RuntimeError(
                "Phase263R msm_pil_init declaration anchor drifted: "
                f"{text.count(decl)}"
            )
        text = text.replace(decl, "\tint i, ret;\n\tsize_t size;", 1)

        if text.count(OLD_SMEM_BLOCK) != 1:
            raise RuntimeError(
                "Phase263R msm_pil_init SMEM block drifted: "
                f"{text.count(OLD_SMEM_BLOCK)}"
            )
        text = text.replace(OLD_SMEM_BLOCK, NEW_SMEM_BLOCK, 1)

    if NEW_TOC_GUARD not in text:
        if text.count(OLD_TOC_GUARD) != 1:
            raise RuntimeError(
                "Phase263R pil_desc_init g_md_toc guard drifted: "
                f"{text.count(OLD_TOC_GUARD)}"
            )
        text = text.replace(OLD_TOC_GUARD, NEW_TOC_GUARD, 1)

    path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    cfg = workspace(root) / "workspace/gki-phase199-out/.config"
    makefile = root / "drivers/a52_pil/Makefile"
    loader = root / "drivers/a52_pil/peripheral-loader.c"

    if not cfg.is_file():
        raise RuntimeError(f"Phase263R config missing: {cfg}")
    if CONFIG_LINE not in cfg.read_text(encoding="utf-8").splitlines():
        raise RuntimeError("Phase263R failed to enable CONFIG_HWSPINLOCK_QCOM=y")

    mk = makefile.read_text(encoding="utf-8")
    if ORDER_MARKER not in mk or GOLDEN_RELATIVE_OBJECT_LINE not in mk:
        raise RuntimeError("Phase263R Golden-relative PIL object order missing")
    if PHASE263_OBJECT_LINE in mk:
        raise RuntimeError("Phase263R old Phase263 PIL object order survived")

    src = loader.read_text(encoding="utf-8")
    required = (
        MINIDUMP_MARKER,
        "int i, ret;",
        "if (IS_ERR(g_md_toc)) {",
        "ret = PTR_ERR(g_md_toc);",
        "g_md_toc = NULL;",
        "continuing without minidump",
        NEW_TOC_GUARD,
    )
    for token in required:
        if token not in src:
            raise RuntimeError(f"Phase263R minidump safety token missing: {token}")
    if "if (PTR_ERR(g_md_toc) == -EPROBE_DEFER)" in src:
        raise RuntimeError("Phase263R legacy poisoned g_md_toc defer path survived")


def self_test() -> None:
    assert CONFIG_LINE == "CONFIG_HWSPINLOCK_QCOM=y"
    assert GOLDEN_RELATIVE_OBJECT_LINE.startswith("obj-y += subsys-pil-tz.o")
    assert GOLDEN_RELATIVE_OBJECT_LINE.endswith("microdump_collector.o")
    assert "IS_ERR_OR_NULL(g_md_toc)" in NEW_TOC_GUARD
    assert "g_md_toc = NULL" in NEW_SMEM_BLOCK
    assert "continuing without minidump" in NEW_SMEM_BLOCK
    print("Phase263R HWSPINLOCK/SMEM/PIL repair self-test: PASS", flush=True)


def apply(root: Path) -> None:
    cfg = workspace(root) / "workspace/gki-phase199-out/.config"
    if not root.is_dir():
        raise RuntimeError(f"Phase263R GKI root missing: {root}")
    if not (root / "drivers/a52_pil/peripheral-loader.c").is_file():
        raise RuntimeError("Phase263R requires Phase263 PIL provider to be staged first")

    set_config(cfg)
    patch_object_order(root)
    patch_minidump_error_lifetime(root)
    verify(root)

    print(f"{MARKER}: Qualcomm HWSPINLOCK -> SMEM -> PIL dependency repaired", flush=True)
    print(f"{ORDER_MARKER}: Golden-relative PIL-TZ/microdump ordering restored", flush=True)
    print(f"{MINIDUMP_MARKER}: g_md_toc ERR_PTR poisoning prevented", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
