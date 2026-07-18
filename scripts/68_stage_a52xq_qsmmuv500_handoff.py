#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"
ARM_SMMU_C = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
ARM_SMMU_H = Path("drivers/iommu/arm/arm-smmu/arm-smmu.h")
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


def normalize_modified_text(gki: Path) -> int:
    """Normalize whitespace in all staged text files before diff validation.

    The replayed Lagoon phases intentionally preserve downstream sources. Some
    of those files carry trailing whitespace, mixed space-before-tab indentation,
    or extra blank lines at EOF. Git rejects those through ``git diff --check``
    before the integrated compile can start, so normalize only files already
    changed by the staged port and leave the pinned base tree untouched.
    """
    raw = subprocess.check_output(
        ["git", "-C", str(gki), "diff", "--name-only", "-z"]
    )
    changed = 0
    for item in raw.split(b"\0"):
        if not item:
            continue
        path = gki / item.decode()
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue

        normalized: list[str] = []
        for line in text.splitlines():
            line = line.rstrip(" \t")
            indent_match = re.match(r"[ \t]*", line)
            if indent_match:
                indent = re.sub(r" +\t", "\t", indent_match.group(0))
                line = indent + line[indent_match.end():]
            normalized.append(line)

        while normalized and not normalized[-1]:
            normalized.pop()

        updated = ("\n".join(normalized) + "\n").encode("utf-8")
        if updated != data:
            path.write_bytes(updated)
            changed += 1
    return changed


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    artifact = args.output.resolve()
    artifact.mkdir(parents=True, exist_ok=True)

    head = command_output("git", "-C", str(gki), "rev-parse", "HEAD")
    if head != GKI_SHA:
        raise SystemExit(f"unexpected GKI revision: {head}")

    source = gki / ARM_SMMU_C
    header = gki / ARM_SMMU_H
    for path in (source, header):
        if not path.is_file():
            raise SystemExit(f"missing pinned GKI source: {path}")

    if COMPATIBLE not in source.read_text(errors="strict"):
        raise SystemExit("Workflow 67 native QSMMUv500 compatible must be staged first")

    feature_anchor = (
        "#define ARM_SMMU_FEAT_EXIDS\t\t(1 << 12)\n"
        "\tu32\t\t\t\tfeatures;\n\n"
        "\tenum arm_smmu_arch_version\tversion;"
    )
    feature_replacement = (
        "#define ARM_SMMU_FEAT_EXIDS\t\t(1 << 12)\n"
        "\tu32\t\t\t\tfeatures;\n\n"
        "\t/* Qualcomm firmware-handoff properties from the A52 ROM DT. */\n"
        "\tbool\t\t\t\tskip_init;\n"
        "\tbool\t\t\t\tuse_3lvl_tables;\n\n"
        "\tenum arm_smmu_arch_version\tversion;"
    )
    replace_once(
        header,
        feature_anchor,
        feature_replacement,
        "QSMMUv500 firmware-handoff state",
    )

    dt_anchor = (
        "\tsmmu->version = data->version;\n"
        "\tsmmu->model = data->model;\n\n"
        "\tlegacy_binding = of_find_property(dev->of_node, \"mmu-masters\", NULL);"
    )
    dt_replacement = (
        "\tsmmu->version = data->version;\n"
        "\tsmmu->model = data->model;\n"
        "\tsmmu->skip_init = of_property_read_bool(dev->of_node,\n"
        "\t\t\t\t\t\t \"qcom,skip-init\");\n"
        "\tsmmu->use_3lvl_tables = of_property_read_bool(dev->of_node,\n"
        "\t\t\t\t\t\t\t\"qcom,use-3-lvl-tables\");\n\n"
        "\tlegacy_binding = of_find_property(dev->of_node, \"mmu-masters\", NULL);"
    )
    replace_once(source, dt_anchor, dt_replacement, "QSMMUv500 DT property parsing")

    reset_anchor = (
        "\t/*\n"
        "\t * Reset stream mapping groups: Initial values mark all SMRn as\n"
        "\t * invalid and all S2CRn as bypass unless overridden.\n"
        "\t */\n"
        "\tfor (i = 0; i < smmu->num_mapping_groups; ++i)\n"
        "\t\tarm_smmu_write_sme(smmu, i);\n\n"
        "\t/* Make sure all context banks are disabled and clear CB_FSR  */\n"
        "\tfor (i = 0; i < smmu->num_context_banks; ++i) {\n"
        "\t\tarm_smmu_write_context_bank(smmu, i);\n"
        "\t\tarm_smmu_cb_write(smmu, i, ARM_SMMU_CB_FSR, ARM_SMMU_FSR_FAULT);\n"
        "\t}\n"
    )
    reset_replacement = (
        "\t/*\n"
        "\t * Preserve firmware stream mappings and context banks when the\n"
        "\t * downstream DT explicitly declares qcom,skip-init.\n"
        "\t */\n"
        "\tif (!smmu->skip_init) {\n"
        "\t\tfor (i = 0; i < smmu->num_mapping_groups; ++i)\n"
        "\t\t\tarm_smmu_write_sme(smmu, i);\n\n"
        "\t\t/* Disable context banks and clear their fault status. */\n"
        "\t\tfor (i = 0; i < smmu->num_context_banks; ++i) {\n"
        "\t\t\tarm_smmu_write_context_bank(smmu, i);\n"
        "\t\t\tarm_smmu_cb_write(smmu, i, ARM_SMMU_CB_FSR,\n"
        "\t\t\t\t\t  ARM_SMMU_FSR_FAULT);\n"
        "\t\t}\n"
        "\t}\n"
    )
    replace_once(source, reset_anchor, reset_replacement, "QSMMUv500 skip-init reset guard")

    impl_reset_anchor = (
        "\tif (smmu->impl && smmu->impl->reset)\n"
        "\t\tsmmu->impl->reset(smmu);"
    )
    impl_reset_replacement = (
        "\tif (!smmu->skip_init && smmu->impl && smmu->impl->reset)\n"
        "\t\tsmmu->impl->reset(smmu);"
    )
    replace_once(
        source,
        impl_reset_anchor,
        impl_reset_replacement,
        "QSMMUv500 skip implementation reset",
    )

    table_anchor = (
        "\t\tif (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH64) {\n"
        "\t\t\tfmt = ARM_64_LPAE_S1;\n"
        "\t\t} else if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_L) {"
    )
    table_replacement = (
        "\t\tif (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH64) {\n"
        "\t\t\tfmt = ARM_64_LPAE_S1;\n"
        "\t\t\tif (smmu->use_3lvl_tables)\n"
        "\t\t\t\tias = min(ias, 39UL);\n"
        "\t\t} else if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_L) {"
    )
    replace_once(
        source,
        table_anchor,
        table_replacement,
        "QSMMUv500 three-level page-table limit",
    )

    normalized_files = normalize_modified_text(gki)

    patch = command_output(
        "git", "-C", str(gki), "diff", "--binary", "--no-ext-diff", "--",
        ARM_SMMU_C.as_posix(), ARM_SMMU_H.as_posix(),
    )
    if not patch:
        raise SystemExit("QSMMUv500 handoff staging produced an empty patch")
    (artifact / "qsmmuv500-handoff.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-qsmmuv500-firmware-handoff-port-not-flashable",
        f"gki_commit={head}",
        f"compatible={COMPATIBLE}",
        "skip_init=ported",
        "skip_init_scope=preserve-firmware-smr-s2cr-context-banks-and-implementation-state",
        "three_level_tables=ported",
        "stage1_iova_limit_bits=39",
        f"normalized_modified_text_file_count={normalized_files}",
        "actlr_table=deferred-multimedia-stream-tuning",
        "tbu_children=deferred-debug-and-ecats-support",
        "source_strategy=extend-native-gki-arm-smmu",
        "flashable=no",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    report = [
        "# A52xq GKI QSMMUv500 firmware-handoff port",
        "",
        "## Ported from the ROM contract",
        "",
        "- `qcom,skip-init` preserves firmware stream mappings and context banks.",
        "- The MMU-500 implementation reset is also skipped when firmware owns initial state.",
        "- `qcom,use-3-lvl-tables` limits AArch64 stage-1 IOVA width to 39 bits.",
        "- The implementation remains the native GKI Qualcomm MMU-500 driver.",
        "",
        "## Deferred intentionally",
        "",
        "- `qcom,actlr` entries tune multimedia stream ranges and are not required to compile the first boot path.",
        "- `qcom,qsmmuv500-tbu` child nodes provide TBU debug, halt, capture-bus, and ECATS tooling.",
        "",
        "## Source hashes after staging",
        "",
        f"- `{ARM_SMMU_C}`: `{sha256(source)}`",
        f"- `{ARM_SMMU_H}`: `{sha256(header)}`",
        "",
    ]
    (artifact / "PORTING-REPORT.md").write_text("\n".join(report))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args)


if __name__ == "__main__":
    main()
