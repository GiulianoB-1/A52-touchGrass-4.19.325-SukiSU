#!/usr/bin/env python3
"""Phase 251: diagnostic-only GMU post-MMIO tail corridor.

Phase250 hardware proves the GPU SMMU correction works through both GMU IOMMU
context-bank attaches. The next first failure is K248 G ops rc=-19 after
K248 M mmio rc=0. Record every remaining error-capable gmu_probe stage without
changing return values, resources, DT, votes, IRQs, or ordering.
"""
from __future__ import annotations

import sys
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
GMU = Path("drivers/gpu/msm/kgsl_gmu.c")
ARM_SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
MARKER = "A52_PHASE251_GMU_POST_MMIO_TAIL_DIAG_V1"
PHASE250 = "A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1"
PHASE248 = "A52_PHASE248_KGSL_GMU_IOMMU_CORRIDOR_V1"
INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old!r}")
    return text.replace(old, new, 1)


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        return text
    if PHASE250 not in text:
        raise RuntimeError(f"{label}: Phase250 recorder admission missing")
    text = one(text,
        'if (strncmp(fmt, "K250", 4) &&\n',
        f'/* {MARKER} */\nif (strncmp(fmt, "K251", 4) &&\n    strncmp(fmt, "K250", 4) &&\n',
        f"{label}: format admission")
    text = one(text,
        'return !strncmp(message, "K250 ", 5) ||\n',
        'return !strncmp(message, "K251 ", 5) ||\n       !strncmp(message, "K250 ", 5) ||\n',
        f"{label}: critical admission")
    return text


def patch_gmu(text: str, label: str) -> str:
    if MARKER in text:
        return text
    if PHASE248 not in text or "K248 M mmio rc=%d" not in text or INCLUDE not in text:
        raise RuntimeError(f"{label}: inherited Phase248 GMU corridor missing")

    text = one(text,
        "\tstruct msm_bus_scale_pdata *bus_scale_table =\n\t\tkgsl_get_bus_scale_table(device);\n\n\tif (bus_scale_table == NULL) {\n",
        "\tstruct msm_bus_scale_pdata *bus_scale_table =\n\t\tkgsl_get_bus_scale_table(device);\n\n"
        f"\t/* {MARKER} */\n"
        "\ta52_ackfr_record(\"K251 B gpu tbl=%d\", bus_scale_table ? 1 : 0);\n\n\tif (bus_scale_table == NULL) {\n",
        f"{label}: GPU BW table")
    text = one(text,
        "\tgmu->pcl = msm_bus_scale_register_client(bus_scale_table);\n\tif (!gmu->pcl) {\n",
        "\tgmu->pcl = msm_bus_scale_register_client(bus_scale_table);\n"
        "\ta52_ackfr_record(\"K251 B gpu pcl=%u\", gmu->pcl);\n\tif (!gmu->pcl) {\n",
        f"{label}: GPU BW client")
    text = one(text,
        "\tcnoc_table = msm_bus_cl_get_pdata(gmu->pdev);\n\tif (cnoc_table == NULL) {\n",
        "\tcnoc_table = msm_bus_cl_get_pdata(gmu->pdev);\n"
        "\ta52_ackfr_record(\"K251 B cnoc tbl=%d\", cnoc_table ? 1 : 0);\n\tif (cnoc_table == NULL) {\n",
        f"{label}: CNOC table")
    text = one(text,
        "\tgmu->ccl = msm_bus_scale_register_client(cnoc_table);\n\tif (!gmu->ccl) {\n",
        "\tgmu->ccl = msm_bus_scale_register_client(cnoc_table);\n"
        "\ta52_ackfr_record(\"K251 B cnoc ccl=%u\", gmu->ccl);\n\tif (!gmu->ccl) {\n",
        f"{label}: CNOC client")

    text = one(text,
        "\tret = kgsl_request_irq(gmu->pdev, \"kgsl_hfi_irq\",\n\t\t\thfi_irq_handler, device);\n",
        "\ta52_ackfr_record(\"K251 G hfiirq in\");\n"
        "\tret = kgsl_request_irq(gmu->pdev, \"kgsl_hfi_irq\",\n\t\t\thfi_irq_handler, device);\n"
        "\ta52_ackfr_record(\"K251 G hfiirq rc=%d\", ret);\n",
        f"{label}: HFI IRQ")
    text = one(text,
        "\tret = kgsl_request_irq(gmu->pdev, \"kgsl_gmu_irq\",\n\t\t\tgmu_irq_handler, device);\n",
        "\ta52_ackfr_record(\"K251 G gmuirq in\");\n"
        "\tret = kgsl_request_irq(gmu->pdev, \"kgsl_gmu_irq\",\n\t\t\tgmu_irq_handler, device);\n"
        "\ta52_ackfr_record(\"K251 G gmuirq rc=%d\", ret);\n",
        f"{label}: GMU IRQ")
    text = one(text,
        "\tdisable_irq(gmu->gmu_interrupt_num);\n\tdisable_irq(hfi->hfi_interrupt_num);\n",
        "\tdisable_irq(gmu->gmu_interrupt_num);\n\tdisable_irq(hfi->hfi_interrupt_num);\n"
        "\ta52_ackfr_record(\"K251 G irqoff\");\n",
        f"{label}: IRQ disable milestone")
    text = one(text,
        "\tret = gmu_gpu_bw_probe(device, gmu);\n",
        "\ta52_ackfr_record(\"K251 G gpubw in\");\n\tret = gmu_gpu_bw_probe(device, gmu);\n"
        "\ta52_ackfr_record(\"K251 G gpubw rc=%d\", ret);\n",
        f"{label}: GPU BW probe")
    text = one(text,
        "\tret = gmu_cnoc_bw_probe(gmu);\n",
        "\ta52_ackfr_record(\"K251 G cnoc in\");\n\tret = gmu_cnoc_bw_probe(gmu);\n"
        "\ta52_ackfr_record(\"K251 G cnoc rc=%d\", ret);\n",
        f"{label}: CNOC BW probe")
    text = one(text,
        "\tret = gmu_rpmh_init(device, gmu, pwr);\n",
        "\ta52_ackfr_record(\"K251 G rpmh in\");\n\tret = gmu_rpmh_init(device, gmu, pwr);\n"
        "\ta52_ackfr_record(\"K251 G rpmh rc=%d\", ret);\n",
        f"{label}: RPMh init")
    text = one(text,
        "\tgmu_acd_probe(device, gmu, node);\n\n\tset_bit(GMU_ENABLED, &device->gmu_core.flags);\n",
        "\tgmu_acd_probe(device, gmu, node);\n\ta52_ackfr_record(\"K251 G acd done\");\n\n"
        "\tset_bit(GMU_ENABLED, &device->gmu_core.flags);\n\ta52_ackfr_record(\"K251 G enabled\");\n",
        f"{label}: GMU completion")

    text = one(text,
        "\tret = gmu_bus_vote_init(gmu, pwr);\n",
        "\ta52_ackfr_record(\"K251 R bus in\");\n\tret = gmu_bus_vote_init(gmu, pwr);\n"
        "\ta52_ackfr_record(\"K251 R bus rc=%d\", ret);\n",
        f"{label}: RPMh bus vote")
    for token, resource in (("gfx", "gfx_res_id"), ("cx", "cx_res_id"), ("mx", "mx_res_id")):
        old = f"\tret = rpmh_arc_cmds(gmu, &{token}_arc, {resource});\n"
        new = f"\ta52_ackfr_record(\"K251 R {token} in\");\n" + old + f"\ta52_ackfr_record(\"K251 R {token} rc=%d\", ret);\n"
        text = one(text, old, new, f"{label}: RPMh {token} ARC")
    text = one(text,
        "\tret = rpmh_arc_votes_init(device, gmu, &gfx_arc, &mx_arc, GPU_ARC_VOTE);\n",
        "\ta52_ackfr_record(\"K251 R gpuvote in\");\n"
        "\tret = rpmh_arc_votes_init(device, gmu, &gfx_arc, &mx_arc, GPU_ARC_VOTE);\n"
        "\ta52_ackfr_record(\"K251 R gpuvote rc=%d\", ret);\n",
        f"{label}: GPU ARC vote")
    text = one(text,
        "\treturn rpmh_arc_votes_init(device, gmu, &cx_arc, &mx_arc, GMU_ARC_VOTE);\n",
        "\ta52_ackfr_record(\"K251 R gmuvote in\");\n"
        "\tret = rpmh_arc_votes_init(device, gmu, &cx_arc, &mx_arc, GMU_ARC_VOTE);\n"
        "\ta52_ackfr_record(\"K251 R gmuvote rc=%d\", ret);\n\treturn ret;\n",
        f"{label}: GMU ARC vote")

    for token in (
        MARKER, "K251 B gpu tbl=%d", "K251 B gpu pcl=%u",
        "K251 B cnoc tbl=%d", "K251 B cnoc ccl=%u",
        "K251 G hfiirq rc=%d", "K251 G gmuirq rc=%d", "K251 G irqoff",
        "K251 G gpubw rc=%d", "K251 G cnoc rc=%d", "K251 G rpmh rc=%d",
        "K251 G enabled", "K251 R bus rc=%d", "K251 R gfx rc=%d",
        "K251 R cx rc=%d", "K251 R mx rc=%d", "K251 R gpuvote rc=%d",
        "K251 R gmuvote rc=%d"):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    return text


def locate(args: list[str]) -> Path:
    base = Path.cwd()
    candidates = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = base / p
        candidates.extend((p, p.parent))
    candidates.extend((base / "workspace/gki-phase199-src", base / "gki/common"))
    hits = []
    seen = set()
    for root in candidates:
        if not all((root / p).is_file() for p in (RECORDER, GMU, ARM_SMMU)):
            continue
        if PHASE250 not in (root / RECORDER).read_text(encoding="utf-8"):
            continue
        if PHASE250 not in (root / ARM_SMMU).read_text(encoding="utf-8"):
            continue
        if PHASE248 not in (root / GMU).read_text(encoding="utf-8"):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one generated Phase250 root, found {len(hits)}")
    return hits[0]


def self_test() -> None:
    rec = f'''/* {PHASE250} */\nif (strncmp(fmt, "K250", 4) &&\nreturn !strncmp(message, "K250 ", 5) ||\n'''
    out = patch_recorder(rec, "fixture")
    assert 'strncmp(fmt, "K251", 4)' in out
    assert '!strncmp(message, "K251 ", 5)' in out
    print("Phase 251 GMU post-MMIO tail diagnostic overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    for rel, fn in ((RECORDER, patch_recorder), (GMU, patch_gmu)):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        path.write_text(fn(text, str(path)), encoding="utf-8")
    print("Phase 251 GMU post-MMIO tail diagnostics applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
