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

DISPLAY_TREES = (
    "techpack/display",
    "drivers/gpu/msm",
    "drivers/clk/qcom/mdss",
)

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


def run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode:
        raise SystemExit(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout}"
        )
    return proc.stdout


def classify(compatible: str) -> list[str]:
    lower = compatible.lower()
    found = [
        name
        for name, tokens in GROUPS.items()
        if any(token in lower for token in tokens)
    ]
    return found or ["other"]


def parse_compatibles(dts: Path) -> list[str]:
    text = dts.read_text(errors="replace")
    result: set[str] = set()
    for prop in re.findall(r"compatible\s*=\s*([^;]+);", text, flags=re.S):
        for quoted in re.findall(r'"([^"]+)"', prop):
            for value in re.split(r"\\0|\x00", quoted):
                value = value.strip()
                if value:
                    result.add(value)
    return sorted(result)


def scan_driver_matches(root: Path, compatibles: list[str]) -> dict[str, list[str]]:
    wanted = set(compatibles)
    hits: dict[str, list[str]] = defaultdict(list)
    for root_name in DRIVER_ROOTS:
        base = root / root_name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            rel = str(path.relative_to(root))
            for compatible in wanted:
                if f'"{compatible}"' in text and rel not in hits[compatible]:
                    hits[compatible].append(rel)
    for values in hits.values():
        values.sort()
    return dict(hits)


def copy_tree(source_root: Path, destination_root: Path, rel: str) -> int:
    src = source_root / rel
    dst = destination_root / rel
    if not src.is_dir():
        raise SystemExit(f"required TouchGrass tree missing: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=True)
    return sum(1 for path in dst.rglob("*") if path.is_file())


def append_once(path: Path, marker: str, content: str) -> None:
    text = path.read_text(errors="replace")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + content.rstrip() + "\n")


def stage_display(touchgrass: Path, gki: Path) -> dict:
    copied_count = sum(copy_tree(touchgrass, gki, rel) for rel in DISPLAY_TREES)

    compat_include = gki / "a52-compat/include"
    if compat_include.exists():
        shutil.rmtree(compat_include)
    shutil.copytree(touchgrass / "include", compat_include, symlinks=True)

    compat_header = gki / "a52-port-compat.h"
    compat_header.write_text(
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

    techpack_mk = gki / "techpack/Makefile"
    techpack_mk.parent.mkdir(parents=True, exist_ok=True)
    techpack_mk.write_text("obj-y += display/\n")

    root_mk = gki / "Makefile"
    root_text = root_mk.read_text(errors="replace")
    boundary = "vmlinux-dirs :="
    if boundary not in root_text:
        raise SystemExit("top-level vmlinux-dirs boundary not found")
    if "A52_PORT_PROBE_TECHPACK" not in root_text:
        root_text = root_text.replace(
            boundary,
            "# A52_PORT_PROBE_TECHPACK\ncore-y += techpack/\n\n" + boundary,
            1,
        )
        root_mk.write_text(root_text)

    display_mk = gki / "techpack/display/Makefile"
    display_text = display_mk.read_text(errors="replace")
    prefix = """# A52 downstream display compile probe
include $(srctree)/techpack/display/config/saipdisp.conf
LINUXINCLUDE += -include $(srctree)/techpack/display/config/saipdispconf.h
subdir-ccflags-y += -include $(srctree)/a52-port-compat.h
subdir-ccflags-y += -I$(srctree)/a52-compat/include
subdir-ccflags-y += -I$(srctree)/a52-compat/include/uapi
"""
    if not display_text.startswith("# A52 downstream display compile probe"):
        display_text = prefix + display_text
    display_text = display_text.replace(
        "obj-$(CONFIG_DRM_MSM) += msm/", "obj-y += msm/"
    )
    display_text = display_text.replace(
        "obj-$(CONFIG_QCOM_MDSS_PLL) += pll/", "obj-y += pll/"
    )
    display_mk.write_text(display_text)

    msm_mk = gki / "techpack/display/msm/Makefile"
    msm_text = msm_mk.read_text(errors="replace")
    if "A52 downstream display compile probe" not in msm_text:
        msm_text = (
            "# A52 downstream display compile probe\n"
            "ccflags-y += -include $(srctree)/a52-port-compat.h\n"
            "ccflags-y += -I$(srctree)/a52-compat/include\n"
            "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n"
            + msm_text
        )
    for symbol in (
        "CONFIG_DRM_MSM_SDE",
        "CONFIG_DRM_SDE_RSC",
        "CONFIG_DRM_MSM_DSI",
        "CONFIG_DSI_PARSER",
        "CONFIG_DRM_MSM",
        "CONFIG_DISPLAY_SAMSUNG",
        "CONFIG_PANEL_S6E3FC3_AMS646YD01_FHD",
    ):
        msm_text = msm_text.replace(f"msm_drm-$({symbol})", "msm_drm-y")
    msm_text = msm_text.replace(
        "obj-$(CONFIG_DRM_MSM)\t+= msm_drm.o", "obj-y += msm_drm.o"
    )
    msm_text = msm_text.replace(
        "ifeq ($(CONFIG_DISPLAY_SAMSUNG),y)", "ifeq (y,y)"
    )
    msm_mk.write_text(msm_text)

    kgsl_mk = gki / "drivers/gpu/msm/Makefile"
    kgsl_text = kgsl_mk.read_text(errors="replace")
    if "A52 downstream KGSL compile probe" not in kgsl_text:
        kgsl_text = (
            "# A52 downstream KGSL compile probe\n"
            "ccflags-y += -include $(srctree)/a52-port-compat.h\n"
            "ccflags-y += -I$(srctree)/a52-compat/include\n"
            "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n"
            + kgsl_text
        )
    kgsl_text = kgsl_text.replace(
        "msm_kgsl_core-$(CONFIG_QCOM_KGSL_IOMMU)", "msm_kgsl_core-y"
    )
    kgsl_text = kgsl_text.replace(
        "msm_adreno-$(CONFIG_QCOM_KGSL_IOMMU)", "msm_adreno-y"
    )
    kgsl_text = kgsl_text.replace("obj-$(CONFIG_QCOM_KGSL)", "obj-y")
    kgsl_mk.write_text(kgsl_text)
    append_once(
        gki / "drivers/gpu/Makefile",
        "A52_PORT_PROBE_KGSL",
        "# A52_PORT_PROBE_KGSL\nobj-y += msm/",
    )

    return {
        "copied_file_count": copied_count,
        "compat_include_file_count": sum(
            1 for path in compat_include.rglob("*") if path.is_file()
        ),
        "compat_header": str(compat_header.relative_to(gki)),
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
    present = {path.name for path in dst.iterdir() if path.is_file()}
    missing = sorted(required - present)
    if missing:
        raise SystemExit("missing secure source files: " + ", ".join(missing))

    objects = [
        name
        for name in (
            "qseecom.o",
            "compat_qseecom.o",
            "qtee_shmbridge.o",
            "smcinvoke.o",
            "qsee_ipc_irq.o",
            "qsee_ipc_irq_bridge.o",
            "secure_buffer.o",
        )
        if (dst / name.replace(".o", ".c")).is_file()
    ]
    (dst / "Makefile").write_text(
        "# A52 downstream secure-service compile probe\n"
        "ccflags-y += -include $(srctree)/a52-port-compat.h\n"
        "ccflags-y += -I$(srctree)/a52-compat/include\n"
        "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n"
        + "obj-y += "
        + " ".join(objects)
        + "\n"
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
    return {
        "copied": copied,
        "compile_policy": "inventory-only-due-to-upstream-symbol-ownership-collisions",
    }


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
        compatible
        for compatible in parse_compatibles(args.dts)
        if compatible.startswith(("qcom,", "samsung,", "stm,"))
        and classify(compatible) != ["other"]
    ]
    touchgrass_hits = scan_driver_matches(touchgrass, compatibles)
    gki_hits = scan_driver_matches(gki, compatibles)

    counts: dict[str, int] = defaultdict(int)
    rows = []
    for compatible in compatibles:
        touchgrass_files = touchgrass_hits.get(compatible, [])
        gki_files = gki_hits.get(compatible, [])
        if gki_files:
            status = "gki-driver-match"
        elif touchgrass_files:
            status = "touchgrass-driver-only"
        else:
            status = "no-driver-owner"
        counts[status] += 1
        rows.append(
            {
                "compatible": compatible,
                "groups": classify(compatible),
                "status": status,
                "touchgrass_driver_files": touchgrass_files,
                "gki_driver_files": gki_files,
            }
        )

    with (output / "driver-ownership-matrix.tsv").open("w") as handle:
        handle.write(
            "compatible\tgroups\tstatus\ttouchgrass_driver_files\tgki_driver_files\n"
        )
        for row in rows:
            handle.write(
                "\t".join(
                    [
                        row["compatible"],
                        ",".join(row["groups"]),
                        row["status"],
                        ";".join(row["touchgrass_driver_files"]),
                        ";".join(row["gki_driver_files"]),
                    ]
                )
                + "\n"
            )

    display = stage_display(touchgrass, gki)
    secure = stage_secure(touchgrass, gki)
    ipc = stage_ipc_inventory(touchgrass, gki)

    report = {
        "status": "downstream-port-probe-staged",
        "flashable": False,
        "hardware_validated": False,
        "touchgrass_commit": run(["git", "rev-parse", "HEAD"], touchgrass).strip(),
        "gki_commit_before_stage": run(["git", "rev-parse", "HEAD"], gki).strip(),
        "compatible_count": len(compatibles),
        "ownership_counts": dict(sorted(counts.items())),
        "display": display,
        "secure": secure,
        "ipc": ipc,
        "rows": rows,
    }
    (output / "port-probe-stage.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )

    (output / "PORT-PROBE.md").write_text(
        "\n".join(
            [
                "# Corrected A52 driver-ownership and port probe",
                "",
                f"- Critical compatible strings: **{len(compatibles)}**",
                f"- Existing 5.10 driver owners: **{counts['gki-driver-match']}**",
                f"- TouchGrass-only driver owners: **{counts['touchgrass-driver-only']}**",
                f"- No exact driver owner: **{counts['no-driver-owner']}**",
                "",
                "Workflow 100 counted DT text as driver support. This report scans only drivers/ and techpack/, and splits dtc \\0 string lists correctly.",
                "",
                "The full TouchGrass display techpack, KGSL, QSEECom, SMCInvoke, QTEE shared-memory bridge and secure-buffer units were staged for a 5.10 compile probe.",
            ]
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
