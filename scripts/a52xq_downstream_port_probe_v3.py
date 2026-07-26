#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

DRIVER_ROOTS = ("drivers", "techpack")
SOURCE_SUFFIXES = {".c", ".h", ".S"}
GROUPS = {
    "display": ("sde", "mdss", "dsi", "panel", "kgsl", "gpu", "dispcc", "gpucc"),
    "secure": ("qsee", "keymaster", "smcinvoke", "qtee", "secure"),
    "ipc": ("glink", "qrtr", "smp2p", "ipcc", "remoteproc", "pil-", "rdbg"),
    "storage": ("ufs", "ice", "scsi", "msm-bus"),
    "power": ("rpmh", "gdsc", "regulator", "gcc", "interconnect"),
    "iommu": ("smmu", "iommu"),
}
DISPLAY_TREES = ("techpack/display", "drivers/gpu/msm", "drivers/clk/qcom/mdss")
SECURE_FILES = (
    "drivers/misc/qseecom.c",
    "drivers/misc/compat_qseecom.c",
    "drivers/misc/compat_qseecom.h",
    "drivers/misc/qseecom_kernel.h",
    "drivers/soc/qcom/qtee_shmbridge.c",
    "drivers/soc/qcom/smcinvoke.c",
    "drivers/soc/qcom/smcinvoke_object.h",
    "drivers/soc/qcom/qsee_ipc_irq.c",
    "drivers/soc/qcom/qsee_ipc_irq_bridge.c",
    "drivers/soc/qcom/secure_buffer.c",
)
IPC_FILES = (
    "drivers/soc/qcom/glink_pkt.c",
    "drivers/soc/qcom/glink_probe.c",
    "drivers/soc/qcom/glink_ssr.c",
    "drivers/soc/qcom/smp2p_sleepstate.c",
    "drivers/soc/qcom/qcom_ipcc.c",
    "drivers/char/rdbg.c",
)


def git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        text=True, stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def classify(value: str) -> list[str]:
    lower = value.lower()
    groups = [name for name, tokens in GROUPS.items() if any(token in lower for token in tokens)]
    return groups or ["other"]


def parse_compatibles(path: Path) -> list[str]:
    text = path.read_text(errors="replace")
    found: set[str] = set()
    for prop in re.findall(r"compatible\s*=\s*([^;]+);", text, flags=re.S):
        for quoted in re.findall(r'"([^"]+)"', prop):
            for value in re.split(r"\\0|\x00", quoted):
                if value.strip():
                    found.add(value.strip())
    return sorted(found)


def scan_drivers(root: Path, compatibles: list[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = defaultdict(list)
    for top in DRIVER_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            rel = str(path.relative_to(root))
            for compatible in compatibles:
                if f'"{compatible}"' in text and rel not in hits[compatible]:
                    hits[compatible].append(rel)
    return {key: sorted(value) for key, value in hits.items()}


def append_once(path: Path, marker: str, text: str) -> None:
    current = path.read_text(errors="replace")
    if marker not in current:
        path.write_text(current.rstrip() + "\n\n" + text.rstrip() + "\n")


def copy_tree(src: Path, dst: Path) -> int:
    if not src.is_dir():
        raise SystemExit(f"missing tree: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=True)
    return sum(1 for item in dst.rglob("*") if item.is_file())


def create_compat_header(gki: Path) -> None:
    (gki / "a52-port-compat.h").write_text(
        """#ifndef A52_PORT_COMPAT_H
#define A52_PORT_COMPAT_H
#define CONFIG_ARCH_LITO 1
#define CONFIG_ARCH_LAGOON 1
#define CONFIG_DRM_MSM 1
#define CONFIG_DRM_MSM_SDE 1
#define CONFIG_DRM_SDE_RSC 1
#define CONFIG_DRM_MSM_DSI 1
#define CONFIG_DSI_PARSER 1
#define CONFIG_DISPLAY_SAMSUNG 1
#define CONFIG_PANEL_S6E3FC3_AMS646YD01_FHD 1
#define CONFIG_QCOM_KGSL 1
#define CONFIG_QCOM_KGSL_IOMMU 1
#define CONFIG_QCOM_MDSS_PLL 1
#define CONFIG_QSEECOM 1
#define CONFIG_QCOM_QSEECOM 1
#define CONFIG_MSM_QTEE_SHMBRIDGE 1
#define CONFIG_MSM_SMCINVOKE 1
#define CONFIG_ION 1
#define CONFIG_ION_SYSTEM_HEAP 1
#endif
"""
    )


def patch_display_makefiles(gki: Path) -> None:
    display_mk = gki / "techpack/display/Makefile"
    text = display_mk.read_text(errors="replace")
    prefix = """# A52 downstream display compile probe
include $(srctree)/techpack/display/config/saipdisp.conf
LINUXINCLUDE += -include $(srctree)/techpack/display/config/saipdispconf.h
subdir-ccflags-y += -include $(srctree)/a52-port-compat.h
subdir-ccflags-y += -I$(srctree)/a52-compat/include
subdir-ccflags-y += -I$(srctree)/a52-compat/include/uapi
"""
    if not text.startswith("# A52 downstream display compile probe"):
        text = prefix + text
    text = text.replace("obj-$(CONFIG_DRM_MSM) += msm/", "obj-y += msm/")
    text = text.replace("obj-$(CONFIG_QCOM_MDSS_PLL) += pll/", "obj-y += pll/")
    display_mk.write_text(text)

    msm_mk = gki / "techpack/display/msm/Makefile"
    text = msm_mk.read_text(errors="replace")
    if "A52 downstream display compile probe" not in text:
        text = (
            "# A52 downstream display compile probe\n"
            "ccflags-y += -include $(srctree)/a52-port-compat.h\n"
            "ccflags-y += -I$(srctree)/a52-compat/include\n"
            "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n" + text
        )
    for symbol in (
        "CONFIG_DRM_MSM_SDE", "CONFIG_DRM_SDE_RSC", "CONFIG_DRM_MSM_DSI",
        "CONFIG_DSI_PARSER", "CONFIG_DRM_MSM", "CONFIG_DISPLAY_SAMSUNG",
        "CONFIG_PANEL_S6E3FC3_AMS646YD01_FHD",
    ):
        text = text.replace(f"msm_drm-$({symbol})", "msm_drm-y")
    text = text.replace("obj-$(CONFIG_DRM_MSM)\t+= msm_drm.o", "obj-y += msm_drm.o")
    text = text.replace("ifeq ($(CONFIG_DISPLAY_SAMSUNG),y)", "ifeq (y,y)")
    msm_mk.write_text(text)


def stage_display(touchgrass: Path, gki: Path) -> dict:
    copied = 0
    for rel in DISPLAY_TREES:
        copied += copy_tree(touchgrass / rel, gki / rel)

    compat = gki / "a52-compat/include"
    copy_tree(touchgrass / "include", compat)
    create_compat_header(gki)
    patch_display_makefiles(gki)

    # Build through drivers/, which is a stable Kbuild entry point in both trees.
    # Keep techpack/display as the canonical include path and duplicate the staged
    # source into an isolated build directory to avoid top-level Makefile edits.
    display_build = gki / "drivers/a52_display"
    copy_tree(gki / "techpack/display", display_build)
    append_once(
        gki / "drivers/Makefile",
        "A52_PORT_PROBE_DISPLAY",
        "# A52_PORT_PROBE_DISPLAY\nobj-y += a52_display/",
    )

    kgsl_mk = gki / "drivers/gpu/msm/Makefile"
    text = kgsl_mk.read_text(errors="replace")
    if "A52 downstream KGSL compile probe" not in text:
        text = (
            "# A52 downstream KGSL compile probe\n"
            "ccflags-y += -include $(srctree)/a52-port-compat.h\n"
            "ccflags-y += -I$(srctree)/a52-compat/include\n"
            "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n" + text
        )
    text = text.replace("msm_kgsl_core-$(CONFIG_QCOM_KGSL_IOMMU)", "msm_kgsl_core-y")
    text = text.replace("msm_adreno-$(CONFIG_QCOM_KGSL_IOMMU)", "msm_adreno-y")
    text = text.replace("obj-$(CONFIG_QCOM_KGSL)", "obj-y")
    kgsl_mk.write_text(text)
    append_once(
        gki / "drivers/gpu/Makefile",
        "A52_PORT_PROBE_KGSL",
        "# A52_PORT_PROBE_KGSL\nobj-y += msm/",
    )

    return {
        "copied_file_count": copied,
        "build_copy_file_count": sum(1 for item in display_build.rglob("*") if item.is_file()),
        "compat_include_file_count": sum(1 for item in compat.rglob("*") if item.is_file()),
        "build_entry": "drivers/a52_display",
    }


def stage_secure(touchgrass: Path, gki: Path) -> dict:
    dst = gki / "drivers/a52_secure"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    copied: list[str] = []
    for rel in SECURE_FILES:
        src = touchgrass / rel
        if src.is_file():
            shutil.copy2(src, dst / src.name)
            copied.append(rel)
    required = {"qseecom.c", "qseecom_kernel.h", "qtee_shmbridge.c", "smcinvoke.c"}
    missing = sorted(required - {item.name for item in dst.iterdir() if item.is_file()})
    if missing:
        raise SystemExit("missing secure files: " + ", ".join(missing))
    objects = [
        name for name in (
            "qseecom.o", "compat_qseecom.o", "qtee_shmbridge.o", "smcinvoke.o",
            "qsee_ipc_irq.o", "qsee_ipc_irq_bridge.o", "secure_buffer.o",
        ) if (dst / name.replace(".o", ".c")).is_file()
    ]
    (dst / "Makefile").write_text(
        "# A52 secure-service compile probe\n"
        "ccflags-y += -include $(srctree)/a52-port-compat.h\n"
        "ccflags-y += -I$(srctree)/a52-compat/include\n"
        "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n"
        "obj-y += " + " ".join(objects) + "\n"
    )
    append_once(
        gki / "drivers/Makefile",
        "A52_PORT_PROBE_SECURE",
        "# A52_PORT_PROBE_SECURE\nobj-y += a52_secure/",
    )
    return {"copied": copied, "objects": objects}


def stage_ipc_inventory(touchgrass: Path, gki: Path) -> dict:
    dst = gki / "drivers/a52_ipc_probe"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    copied: list[str] = []
    for rel in IPC_FILES:
        src = touchgrass / rel
        if src.is_file():
            shutil.copy2(src, dst / src.name)
            copied.append(rel)
    return {"copied": copied, "compile_policy": "inventory-only"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--dts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    touchgrass = args.touchgrass.resolve()
    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    compatibles = [
        value for value in parse_compatibles(args.dts)
        if value.startswith(("qcom,", "samsung,", "stm,")) and classify(value) != ["other"]
    ]
    tg_hits = scan_drivers(touchgrass, compatibles)
    gki_hits = scan_drivers(gki, compatibles)
    counts: dict[str, int] = defaultdict(int)
    rows = []
    for compatible in compatibles:
        tg = tg_hits.get(compatible, [])
        current = gki_hits.get(compatible, [])
        status = "gki-driver-match" if current else ("touchgrass-driver-only" if tg else "no-driver-owner")
        counts[status] += 1
        rows.append({
            "compatible": compatible,
            "groups": classify(compatible),
            "status": status,
            "touchgrass_driver_files": tg,
            "gki_driver_files": current,
        })

    with (output / "driver-ownership-matrix.tsv").open("w") as handle:
        handle.write("compatible\tgroups\tstatus\ttouchgrass_driver_files\tgki_driver_files\n")
        for row in rows:
            handle.write("\t".join((
                row["compatible"], ",".join(row["groups"]), row["status"],
                ";".join(row["touchgrass_driver_files"]), ";".join(row["gki_driver_files"]),
            )) + "\n")

    report = {
        "status": "downstream-port-probe-staged",
        "flashable": False,
        "hardware_validated": False,
        "touchgrass_commit": git_head(touchgrass),
        "gki_commit_before_stage": git_head(gki),
        "compatible_count": len(compatibles),
        "ownership_counts": dict(sorted(counts.items())),
        "display": stage_display(touchgrass, gki),
        "secure": stage_secure(touchgrass, gki),
        "ipc": stage_ipc_inventory(touchgrass, gki),
        "rows": rows,
    }
    (output / "port-probe-stage.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (output / "PORT-PROBE.md").write_text(
        "# Corrected A52 driver-ownership and port probe\n\n"
        f"- Critical compatible strings: **{len(compatibles)}**\n"
        f"- Existing 5.10 driver owners: **{counts['gki-driver-match']}**\n"
        f"- TouchGrass-only driver owners: **{counts['touchgrass-driver-only']}**\n"
        f"- No exact driver owner: **{counts['no-driver-owner']}**\n\n"
        "The full downstream display, KGSL and secure-service units were staged through stable drivers/ Kbuild entries.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
