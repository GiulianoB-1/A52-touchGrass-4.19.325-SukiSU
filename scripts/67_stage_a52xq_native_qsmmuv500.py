#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"
ARM_SMMU_C = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
ARM_SMMU_IMPL_C = Path("drivers/iommu/arm/arm-smmu/arm-smmu-impl.c")
COMPATIBLE = "qcom,qsmmu-v500"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(errors="strict")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    artifact = args.output.resolve()
    artifact.mkdir(parents=True, exist_ok=True)

    head = command_output("git", "-C", str(gki), "rev-parse", "HEAD")
    if head != GKI_SHA:
        raise SystemExit(f"unexpected GKI revision: {head}")

    arm_smmu = gki / ARM_SMMU_C
    impl = gki / ARM_SMMU_IMPL_C
    for path in (arm_smmu, impl):
        if not path.is_file():
            raise SystemExit(f"missing pinned GKI source: {path}")

    match_anchor = (
        '\t{ .compatible = "nvidia,smmu-500", .data = &arm_mmu500 },\n'
        '\t{ .compatible = "qcom,smmu-v2", .data = &qcom_smmuv2 },'
    )
    match_replacement = (
        '\t{ .compatible = "nvidia,smmu-500", .data = &arm_mmu500 },\n'
        '\t{ .compatible = "qcom,qsmmu-v500", .data = &arm_mmu500 },\n'
        '\t{ .compatible = "qcom,smmu-v2", .data = &qcom_smmuv2 },'
    )
    replace_once(
        arm_smmu,
        match_anchor,
        match_replacement,
        "A52 QSMMUv500 OF match",
    )

    impl_anchor = (
        '\tif (of_device_is_compatible(np, "qcom,sdm845-smmu-500") ||\n'
        '\t    of_device_is_compatible(np, "qcom,sc7180-smmu-500") ||'
    )
    impl_replacement = (
        '\tif (of_device_is_compatible(np, "qcom,qsmmu-v500") ||\n'
        '\t    of_device_is_compatible(np, "qcom,sdm845-smmu-500") ||\n'
        '\t    of_device_is_compatible(np, "qcom,sc7180-smmu-500") ||'
    )
    replace_once(
        impl,
        impl_anchor,
        impl_replacement,
        "A52 QSMMUv500 Qualcomm implementation selection",
    )

    patch = command_output(
        "git", "-C", str(gki), "diff", "--binary", "--no-ext-diff", "--",
        ARM_SMMU_C.as_posix(), ARM_SMMU_IMPL_C.as_posix(),
    )
    if not patch:
        raise SystemExit("native QSMMUv500 staging produced an empty patch")
    (artifact / "native-qsmmuv500.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-native-qsmmuv500-object-probe-not-flashable",
        f"gki_commit={head}",
        f"compatible={COMPATIBLE}",
        "match_model=ARM_MMU500",
        "implementation=qcom_smmu_impl",
        "iommu_cells_supported=two",
        "source_strategy=extend-native-gki-arm-smmu-not-transplant-legacy-driver",
        "skip_init_semantics=not-yet-ported",
        "actlr_table=not-yet-ported",
        "tbu_children=not-yet-ported",
        "image_build=no",
        "boot_test=no",
        "flashable=no",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    report = [
        "# A52xq native GKI QSMMUv500 object probe",
        "",
        "## Purpose",
        "",
        "This probe extends the pinned Android 12 GKI 5.10 ARM-SMMU driver with the",
        "A52 ROM compatible `qcom,qsmmu-v500` and selects the existing Qualcomm",
        "MMU-500 implementation. It does not transplant the legacy 4.19 driver.",
        "",
        "## Device-tree compatibility",
        "",
        "- Parent compatible: `qcom,qsmmu-v500`",
        "- GKI model: `ARM_MMU500`",
        "- Implementation: `qcom_smmu_impl`",
        "- The GKI `of_xlate` path already accepts the A52 two-cell SID and mask form.",
        "",
        "## Deliberately unresolved",
        "",
        "- `qcom,skip-init` firmware handoff semantics",
        "- `qcom,actlr` per-stream context-bank tuning",
        "- `qcom,qsmmuv500-tbu` child registration and optional debug/ECATS support",
        "- Full Image link, DT integration, packaging, and device boot",
        "",
        "## Safety",
        "",
        "- Object-only compile probe",
        "- No Image, DTB, boot image, or flashable ZIP is produced",
        "",
        "## Source hashes after staging",
        "",
        f"- `{ARM_SMMU_C}`: `{sha256(arm_smmu)}`",
        f"- `{ARM_SMMU_IMPL_C}`: `{sha256(impl)}`",
        "",
    ]
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args)


if __name__ == "__main__":
    main()
