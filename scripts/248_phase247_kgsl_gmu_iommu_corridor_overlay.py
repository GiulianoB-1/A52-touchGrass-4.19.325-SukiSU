#!/usr/bin/env python3
"""Phase 248: diagnostic-only KGSL -> GMU -> IOMMU probe corridor.

Phase247 hardware proves CAMCC now returns and both GPU GX/CX GDSCs bind.
The first KGSL probe defers until qfprom/NVMEM becomes ready; the second probe
enters adreno_probe() with all driver-core suppliers ready and then stops.
This overlay adds bounded critical K248 records around the unchanged Adreno,
GMU core, GMU probe and GMU IOMMU context-bank attach path.  It changes no
return value, ordering, DT property, IOMMU mapping, power vote, regulator vote,
fw_devlink state or recorder transport.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
ADRENO = Path("drivers/gpu/msm/adreno.c")
GMU_CORE = Path("drivers/gpu/msm/kgsl_gmu_core.c")
GMU = Path("drivers/gpu/msm/kgsl_gmu.c")
CAMCC = Path("drivers/clk/qcom/camcc-lagoon.c")
CORE = Path("drivers/base/core.c")
INIT_MAIN = Path("init/main.c")

MARKER = "A52_PHASE248_KGSL_GMU_IOMMU_CORRIDOR_V1"
PHASE247 = "A52_PHASE247_CAMCC_DENSE_HWS_V1"
PHASE246 = "CXF246 S n=%d f=%ps"
PHASE229 = "A52_PHASE229_KGSL_PLATFORM_PATH"
PERMISSIVE = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"
INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>\n"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old!r}")
    return text.replace(old, new, 1)


def ensure_include(text: str, anchor: str, label: str) -> str:
    if INCLUDE in text:
        return text
    return one(text, anchor, anchor + INCLUDE, f"{label}: recorder include")


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    old_filter = 'if (strncmp(fmt, "CXF246", 6) &&\n'
    new_filter = (
        f'/* {MARKER} */\n'
        'if (strncmp(fmt, "K248", 4) &&\n'
        '    strncmp(fmt, "CXF246", 6) &&\n'
    )
    text = one(text, old_filter, new_filter, f"{label}: format filter")
    old_crit = 'return !strncmp(message, "CXF246 ", 7) ||\n'
    new_crit = (
        'return !strncmp(message, "K248 ", 5) ||\n'
        '       !strncmp(message, "CXF246 ", 7) ||\n'
    )
    text = one(text, old_crit, new_crit, f"{label}: critical filter")
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER,
        'strncmp(fmt, "K248", 4)',
        '!strncmp(message, "K248 ", 5)',
        'strncmp(fmt, "CXF246", 6)',
        '!strncmp(message, "CXF246 ", 7)',
        'strncmp(fmt, "CXF243", 6)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_adreno(text: str, label: str) -> str:
    if "K248 A ef rc=%d" in text:
        validate_adreno(text, label)
        return text
    if PHASE229 not in text or "KGPPOST 229 probe-in" not in text:
        raise RuntimeError(f"{label}: Phase229 KGSL instrumentation missing")

    text = one(
        text,
        "\tstatus = adreno_probe_efuse(pdev, adreno_dev);\n",
        "\tstatus = adreno_probe_efuse(pdev, adreno_dev);\n"
        "\ta52_ackfr_record(\"K248 A ef rc=%d\", status);\n",
        f"{label}: efuse",
    )
    text = one(
        text,
        "\tif (adreno_identify_gpu(adreno_dev))\n",
        "\ta52_ackfr_record(\"K248 A id in\");\n"
        "\tif (adreno_identify_gpu(adreno_dev))\n",
        f"{label}: identify enter",
    )
    text = one(
        text,
        "\tstatus = adreno_of_get_power(adreno_dev, pdev);\n",
        "\ta52_ackfr_record(\"K248 A id ok\");\n"
        "\tstatus = adreno_of_get_power(adreno_dev, pdev);\n"
        "\ta52_ackfr_record(\"K248 A pwr rc=%d\", status);\n",
        f"{label}: power",
    )
    text = one(
        text,
        "\tstatus = gmu_core_probe(device);\n",
        "\ta52_ackfr_record(\"K248 A gmu in\");\n"
        "\tstatus = gmu_core_probe(device);\n"
        "\ta52_ackfr_record(\"K248 A gmu rc=%d\", status);\n",
        f"{label}: gmu",
    )
    text = one(
        text,
        "\tstatus = kgsl_device_platform_probe(device);\n",
        "\ta52_ackfr_record(\"K248 A plat in\");\n"
        "\tstatus = kgsl_device_platform_probe(device);\n"
        "\ta52_ackfr_record(\"K248 A plat rc=%d\", status);\n",
        f"{label}: platform probe",
    )
    text = one(
        text,
        "\tstatus = kgsl_allocate_global(device, &device->memstore,\n",
        "\ta52_ackfr_record(\"K248 A mem in\");\n"
        "\tstatus = kgsl_allocate_global(device, &device->memstore,\n",
        f"{label}: memstore enter",
    )
    text = one(
        text,
        "\tstatus = adreno_ringbuffer_probe(adreno_dev);\n",
        "\ta52_ackfr_record(\"K248 A mem rc=%d\", status);\n"
        "\tstatus = adreno_ringbuffer_probe(adreno_dev);\n"
        "\ta52_ackfr_record(\"K248 A rb rc=%d\", status);\n",
        f"{label}: ringbuffer",
    )
    text = one(
        text,
        "\tstatus = adreno_dispatcher_init(adreno_dev);\n",
        "\tstatus = adreno_dispatcher_init(adreno_dev);\n"
        "\ta52_ackfr_record(\"K248 A dsp rc=%d\", status);\n",
        f"{label}: dispatcher",
    )
    validate_adreno(text, label)
    return text


def validate_adreno(text: str, label: str) -> None:
    for token in (
        "K248 A ef rc=%d", "K248 A id in", "K248 A id ok",
        "K248 A pwr rc=%d", "K248 A gmu in", "K248 A gmu rc=%d",
        "K248 A plat in", "K248 A plat rc=%d", "K248 A mem in",
        "K248 A mem rc=%d", "K248 A rb rc=%d", "K248 A dsp rc=%d",
        "a52_r229_probe_return",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_gmu_core(text: str, label: str) -> str:
    if "K248 G ops in" in text:
        validate_gmu_core(text, label)
        return text
    text = ensure_include(text, "#include <linux/of.h>\n", label)
    text = one(
        text,
        "\t\tret = gmu_core_ops->probe(device, node);\n",
        "\t\ta52_ackfr_record(\"K248 G ops in t=%d\", device->gmu_core.type);\n"
        "\t\tret = gmu_core_ops->probe(device, node);\n"
        "\t\ta52_ackfr_record(\"K248 G ops rc=%d\", ret);\n",
        f"{label}: ops probe",
    )
    validate_gmu_core(text, label)
    return text


def validate_gmu_core(text: str, label: str) -> None:
    for token in (INCLUDE.strip(), "K248 G ops in t=%d", "K248 G ops rc=%d", "gmu_core_ops->probe(device, node)"):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_gmu(text: str, label: str) -> str:
    if "K248 M iommu in" in text:
        validate_gmu(text, label)
        return text
    if INCLUDE not in text:
        anchor = '#include "kgsl_gmu_core.h"\n'
        if anchor not in text:
            anchor = '#include "kgsl_gmu.h"\n'
        text = ensure_include(text, anchor, label)

    text = one(
        text,
        "\tof_dma_configure(&gmu->pdev->dev, node, true);\n",
        "\tof_dma_configure(&gmu->pdev->dev, node, true);\n"
        "\ta52_ackfr_record(\"K248 M dma\");\n",
        f"{label}: gmu dma",
    )
    text = one(
        text,
        "\tret = gmu_regulators_probe(gmu, node);\n",
        "\tret = gmu_regulators_probe(gmu, node);\n"
        "\ta52_ackfr_record(\"K248 M reg rc=%d\", ret);\n",
        f"{label}: regulators",
    )
    text = one(
        text,
        "\tret = gmu_clocks_probe(gmu, node);\n",
        "\tret = gmu_clocks_probe(gmu, node);\n"
        "\ta52_ackfr_record(\"K248 M clk rc=%d\", ret);\n",
        f"{label}: clocks",
    )
    text = one(
        text,
        "\tret = gmu_iommu_init(gmu, node);\n",
        "\ta52_ackfr_record(\"K248 M iommu in\");\n"
        "\tret = gmu_iommu_init(gmu, node);\n"
        "\ta52_ackfr_record(\"K248 M iommu rc=%d\", ret);\n",
        f"{label}: iommu",
    )
    text = one(
        text,
        "\tret = gmu_tcm_init(gmu);\n",
        "\tret = gmu_tcm_init(gmu);\n"
        "\ta52_ackfr_record(\"K248 M tcm rc=%d\", ret);\n",
        f"{label}: tcm",
    )
    text = one(
        text,
        "\tret = gmu_reg_probe(device);\n",
        "\tret = gmu_reg_probe(device);\n"
        "\ta52_ackfr_record(\"K248 M mmio rc=%d\", ret);\n",
        f"{label}: mmio",
    )

    text = one(
        text,
        "\tof_platform_populate(node, NULL, NULL, &gmu->pdev->dev);\n",
        "\ta52_ackfr_record(\"K248 I pop in\");\n"
        "\tof_platform_populate(node, NULL, NULL, &gmu->pdev->dev);\n"
        "\ta52_ackfr_record(\"K248 I pop out\");\n",
        f"{label}: iommu populate",
    )
    text = one(
        text,
        "\t\t\tctx = &gmu_ctx[cbs[i].index];\n"
        "\t\t\tret = gmu_iommu_cb_probe(gmu, ctx, child);\n",
        "\t\t\tctx = &gmu_ctx[cbs[i].index];\n"
        "\t\t\ta52_ackfr_record(\"K248 I cb in i=%d\", cbs[i].index);\n"
        "\t\t\tret = gmu_iommu_cb_probe(gmu, ctx, child);\n"
        "\t\t\ta52_ackfr_record(\"K248 I cb rc=%d i=%d\", ret, cbs[i].index);\n",
        f"{label}: cb loop",
    )
    text = one(
        text,
        "\treturn 0;\n}\n\n/*\n * gmu_memory_close()",
        "\ta52_ackfr_record(\"K248 I done\");\n"
        "\treturn 0;\n}\n\n/*\n * gmu_memory_close()",
        f"{label}: iommu done",
    )

    text = one(
        text,
        "\tdev = &pdev->dev;\n"
        "\tof_dma_configure(dev, node, true);\n",
        "\ta52_ackfr_record(\"K248 C p n=%.12s ok=%d\", node->name, pdev ? 1 : 0);\n"
        "\tdev = &pdev->dev;\n"
        "\tof_dma_configure(dev, node, true);\n"
        "\ta52_ackfr_record(\"K248 C dma n=%.12s\", node->name);\n",
        f"{label}: cb dma",
    )
    text = one(
        text,
        "\tctx->domain = iommu_domain_alloc(&platform_bus_type);\n",
        "\tctx->domain = iommu_domain_alloc(&platform_bus_type);\n"
        "\ta52_ackfr_record(\"K248 C dom n=%.12s ok=%d\", node->name, ctx->domain ? 1 : 0);\n",
        f"{label}: domain alloc",
    )
    text = one(
        text,
        "\tret = iommu_attach_device(ctx->domain, dev);\n",
        "\ta52_ackfr_record(\"K248 C att in n=%.12s\", node->name);\n"
        "\tret = iommu_attach_device(ctx->domain, dev);\n"
        "\ta52_ackfr_record(\"K248 C att rc=%d n=%.12s\", ret, node->name);\n",
        f"{label}: attach",
    )
    validate_gmu(text, label)
    return text


def validate_gmu(text: str, label: str) -> None:
    for token in (
        INCLUDE.strip(),
        "K248 M dma", "K248 M reg rc=%d", "K248 M clk rc=%d",
        "K248 M iommu in", "K248 M iommu rc=%d", "K248 M tcm rc=%d",
        "K248 M mmio rc=%d", "K248 I pop in", "K248 I pop out",
        "K248 I cb in i=%d", "K248 I cb rc=%d i=%d", "K248 I done",
        "K248 C p n=%.12s ok=%d", "K248 C dma n=%.12s",
        "K248 C dom n=%.12s ok=%d", "K248 C att in n=%.12s",
        "K248 C att rc=%d n=%.12s",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        paths = {p: root / p for p in (RECORDER, ADRENO, GMU_CORE, GMU, CAMCC, CORE, INIT_MAIN)}
        if not all(path.is_file() for path in paths.values()):
            continue
        if PHASE247 not in paths[CAMCC].read_text(encoding="utf-8"):
            continue
        if PHASE246 not in paths[INIT_MAIN].read_text(encoding="utf-8"):
            continue
        if PERMISSIVE not in paths[CORE].read_text(encoding="utf-8"):
            continue
        if PHASE229 not in paths[ADRENO].read_text(encoding="utf-8"):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        rendered = ", ".join(map(str, hits)) or "none"
        raise RuntimeError(f"expected one generated Phase247 root, found {len(hits)}: {rendered}")
    return hits[0]


def self_test() -> None:
    recorder = (
        'if (strncmp(fmt, "CXF246", 6) &&\n'
        '    strncmp(fmt, "CXF243", 6) &&\n'
        'return !strncmp(message, "CXF246 ", 7) ||\n'
        '       !strncmp(message, "CXF243 ", 7) ||\n'
    )
    rec2 = patch_recorder(recorder, "fixture/rec")
    assert patch_recorder(rec2, "fixture/rec2") == rec2

    adreno = f'''/* {PHASE229} */
{INCLUDE}KGPPOST 229 probe-in
static int adreno_probe(void) {{
\tstatus = adreno_probe_efuse(pdev, adreno_dev);
\tif (status) return a52_r229_probe_return(status);
\tif (adreno_identify_gpu(adreno_dev))
\t\treturn a52_r229_probe_return(-ENODEV);
\tstatus = adreno_of_get_power(adreno_dev, pdev);
\tstatus = gmu_core_probe(device);
\tstatus = kgsl_device_platform_probe(device);
\tstatus = kgsl_allocate_global(device, &device->memstore,
\t\tKGSL_MEMSTORE_SIZE, 0, priv, "memstore");
\tstatus = adreno_ringbuffer_probe(adreno_dev);
\tstatus = adreno_dispatcher_init(adreno_dev);
}}
'''
    a2 = patch_adreno(adreno, "fixture/adreno")
    assert patch_adreno(a2, "fixture/adreno2") == a2

    core = '''#include <linux/of.h>\nint gmu_core_probe(void) {\n\tif (gmu_core_ops && gmu_core_ops->probe) {\n\t\tret = gmu_core_ops->probe(device, node);\n\t}\n}\n'''
    c2 = patch_gmu_core(core, "fixture/gmu_core")
    assert patch_gmu_core(c2, "fixture/gmu_core2") == c2

    gmu = '''#include "kgsl_gmu_core.h"\n
static int gmu_iommu_cb_probe(void) {
\tstruct platform_device *pdev = of_find_device_by_node(node);
\tdev = &pdev->dev;
\tof_dma_configure(dev, node, true);
\tctx->domain = iommu_domain_alloc(&platform_bus_type);
\tret = iommu_attach_device(ctx->domain, dev);
}
static int gmu_iommu_init(void) {
\tof_platform_populate(node, NULL, NULL, &gmu->pdev->dev);
\t\t\tctx = &gmu_ctx[cbs[i].index];
\t\t\tret = gmu_iommu_cb_probe(gmu, ctx, child);
\treturn 0;
}

/*
 * gmu_memory_close()
 */
static int gmu_probe(void) {
\tof_dma_configure(&gmu->pdev->dev, node, true);
\tret = gmu_regulators_probe(gmu, node);
\tret = gmu_clocks_probe(gmu, node);
\tret = gmu_iommu_init(gmu, node);
\tret = gmu_tcm_init(gmu);
\tret = gmu_reg_probe(device);
}
'''
    g2 = patch_gmu(gmu, "fixture/gmu")
    assert patch_gmu(g2, "fixture/gmu2") == g2

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "gki/common"
        contents = {
            RECORDER: recorder,
            ADRENO: adreno,
            GMU_CORE: core,
            GMU: gmu,
            CAMCC: PHASE247 + "\n",
            CORE: PERMISSIVE + "\n",
            INIT_MAIN: PHASE246 + "\n",
        }
        for rel, data in contents.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
        assert locate([], Path(td)).resolve() == root.resolve()

    print("Phase 248 KGSL/GMU/IOMMU corridor overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    patches = (
        (RECORDER, patch_recorder),
        (ADRENO, patch_adreno),
        (GMU_CORE, patch_gmu_core),
        (GMU, patch_gmu),
    )
    for rel, fn in patches:
        path = root / rel
        before = path.read_text(encoding="utf-8")
        path.write_text(fn(before, str(path)), encoding="utf-8")
    print("Phase 248 KGSL -> GMU -> IOMMU diagnostic corridor applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
