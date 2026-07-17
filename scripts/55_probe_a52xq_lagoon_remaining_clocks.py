#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"
TOUCHGRASS_SHA = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"

PROBES = {
    "npucc-lagoon": (
        "drivers/clk/qcom/npucc-lagoon.c",
        "drivers/clk/qcom/npucc-lagoon.o",
        "CONFIG_NPU_CC_LAGOON",
        "Lagoon NPU clock controller",
    ),
    "clk-debug-core": (
        "drivers/clk/qcom/clk-debug.c",
        "drivers/clk/qcom/clk-debug.o",
        "CONFIG_QCOM_CLK_DEBUG",
        "Qualcomm debug clock measurement core",
    ),
    "debugcc-lagoon": (
        "drivers/clk/qcom/debugcc-lagoon.c",
        "drivers/clk/qcom/debugcc-lagoon.o",
        "CONFIG_DEBUG_CC_LAGOON",
        "Lagoon debug clock controller",
    ),
}

COPY_ONLY = (
    "drivers/clk/qcom/clk-debug.h",
    "include/dt-bindings/clock/qcom,npucc-lagoon.h",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(errors="replace")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n")


def strip_npu_compat(path: Path) -> None:
    lines = path.read_text(errors="replace").splitlines()
    result: list[str] = []
    index = 0
    obsolete_pll_configs = {
        ".config = &npu_cc_pll0_config,",
        ".config = &npu_cc_pll1_config,",
        ".config = &npu_q6ss_pll_config,",
    }

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped == '#include "vdd-level-lagoon.h"':
            index += 1
            continue
        if "DEFINE_VDD_REGULATORS(" in line or "DEFINE_VDD_REGS_INIT(" in line:
            index += 1
            continue
        if stripped in obsolete_pll_configs:
            index += 1
            continue
        if ".enable_safe_config =" in line or ".cal_l =" in line:
            index += 1
            continue
        if any(token in line for token in (
            ".custom_reg_offset =", ".custom_reg_val =", ".num_custom_reg ="
        )):
            index += 1
            continue
        if "&clk_branch2_hw_ctl_ops" in line:
            result.append(line.replace("&clk_branch2_hw_ctl_ops", "&clk_branch2_ops"))
            index += 1
            continue

        if ".vdd_class =" in line:
            index += 1
            if index < len(lines) and ".num_rate_max =" in lines[index]:
                index += 1
            if index >= len(lines) or ".rate_max =" not in lines[index]:
                raise SystemExit(f"malformed VDD rate table in {path}")
            depth = lines[index].count("{") - lines[index].count("}")
            index += 1
            while index < len(lines) and depth > 0:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            continue

        if re.match(r"^vdd_[A-Za-z0-9_]+\.regulator\[0\] = devm_regulator_get", stripped):
            index += 1
            if index < len(lines) and lines[index].lstrip().startswith("if (IS_ERR("):
                depth = lines[index].count("{") - lines[index].count("}")
                index += 1
                while index < len(lines) and depth > 0:
                    depth += lines[index].count("{") - lines[index].count("}")
                    index += 1
            continue

        result.append(line)
        index += 1

    text = "\n".join(result) + "\n"
    forbidden = (
        '"vdd-level-lagoon.h"',
        "DEFINE_VDD_REGULATORS",
        "DEFINE_VDD_REGS_INIT",
        ".vdd_class =",
        ".num_rate_max =",
        ".rate_max =",
        ".enable_safe_config =",
        ".cal_l =",
        ".custom_reg_offset =",
        ".custom_reg_val =",
        ".num_custom_reg =",
        "clk_branch2_hw_ctl_ops",
        "devm_regulator_get",
        *obsolete_pll_configs,
    )
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f"unsupported NPU clock tokens remain in {path}: {leftovers}")
    path.write_text(text)


def preserve_npu_crc_calibration(path: Path) -> None:
    text = path.read_text(errors="replace")
    function_marker = (
        "static int npu_clocks_lagoon_probe(struct platform_device *pdev,\n"
        "\t\t\t\t\tconst struct qcom_cc_desc *desc)\n"
        "{"
    )
    if function_marker not in text:
        raise SystemExit("NPU probe function marker not found")

    function_start = text.index(function_marker)
    declaration = "\tint ret;"
    declaration_pos = text.find(declaration, function_start)
    if declaration_pos < 0:
        raise SystemExit("NPU probe return declaration not found")
    text = (
        text[:declaration_pos]
        + declaration
        + "\n\tsize_t i;"
        + text[declaration_pos + len(declaration):]
    )

    marker = (
        '\tif (!strcmp("cc", desc->config->name)) {\n'
        "\t\tclk_fabia_pll_configure(&npu_cc_pll0, regmap,"
    )
    replacement = (
        '\tif (!strcmp("cc", desc->config->name)) {\n'
        "\t\t/* Preserve the downstream CRC calibration register writes. */\n"
        "\t\tfor (i = 0; i < ARRAY_SIZE(crc_reg_offset); i++)\n"
        "\t\t\tregmap_write(regmap, crc_reg_offset[i], crc_reg_val[i]);\n\n"
        "\t\tclk_fabia_pll_configure(&npu_cc_pll0, regmap,"
    )
    if marker not in text:
        raise SystemExit("NPU main clock-domain marker not found")
    text = text.replace(marker, replacement, 1)
    path.write_text(text)


def adapt_clk_debug_header(path: Path) -> None:
    text = path.read_text(errors="replace")
    include_marker = "#include <linux/platform_device.h>\n"
    if "#include <linux/bitops.h>" not in text:
        text = text.replace(include_marker, include_marker + "#include <linux/bitops.h>\n", 1)
    define_block = (
        "\n#ifndef CLK_IS_MEASURE\n"
        "#define CLK_IS_MEASURE BIT(16)\n"
        "#endif\n"
    )
    if "#define CLK_IS_MEASURE" not in text:
        text = text.replace('#include "../clk.h"\n', '#include "../clk.h"\n' + define_block, 1)
    path.write_text(text)


def adapt_clk_debug_core(path: Path) -> None:
    lines = path.read_text(errors="replace").splitlines()
    result: list[str] = []
    index = 0

    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "#include <linux/msm-bus.h>":
            index += 1
            continue
        if stripped in ("if (meas->bus_cl_id)", "if (hw->init->bus_cl_id)"):
            index += 1
            while index < len(lines):
                current = lines[index].strip()
                if "msm_bus_scale_client_update_request" in current:
                    index += 1
                    while index < len(lines) and not lines[index - 1].rstrip().endswith(");"):
                        index += 1
                    break
                if current:
                    break
                index += 1
            continue
        if "msm_bus_scale_client_update_request" in stripped:
            index += 1
            while index < len(lines) and not lines[index - 1].rstrip().endswith(");"):
                index += 1
            continue
        result.append(lines[index])
        index += 1

    text = "\n".join(result) + "\n"
    if "#include <linux/debugfs.h>" not in text:
        text = text.replace(
            "#include <linux/clk-provider.h>\n",
            "#include <linux/clk-provider.h>\n#include <linux/debugfs.h>\n",
            1,
        )

    vote_pattern = re.compile(
        r"void clk_debug_bus_vote\(struct clk_hw \*hw, bool enable\)\n\{.*?\n\}",
        re.S,
    )
    vote_replacement = (
        "void clk_debug_bus_vote(struct clk_hw *hw, bool enable)\n"
        "{\n"
        "\t/* MSM bus scaling is not part of GKI. Measurement remains optional. */\n"
        "\t(void)hw;\n"
        "\t(void)enable;\n"
        "}"
    )
    text, count = vote_pattern.subn(vote_replacement, text, count=1)
    if count != 1:
        raise SystemExit("clk_debug_bus_vote function was not replaced")

    measure_get_old = (
        "static int clk_debug_measure_get(void *data, u64 *val)\n"
        "{\n"
        "\tstruct clk_hw *hw = data;\n"
        "\tstruct clk_debug_mux *meas = to_clk_measure(measure);\n"
    )
    measure_get_new = (
        "static int clk_debug_measure_get(void *data, u64 *val)\n"
        "{\n"
        "\tstruct clk_hw *hw = data;\n"
    )
    if measure_get_old not in text:
        raise SystemExit("clk_debug_measure_get compatibility marker not found")
    text = text.replace(measure_get_old, measure_get_new, 1)

    measure_add_tail = "err:\n}\nEXPORT_SYMBOL(clk_debug_measure_add);"
    if measure_add_tail not in text:
        raise SystemExit("clk_debug_measure_add error label marker not found")
    text = text.replace(
        measure_add_tail,
        "err:\n\treturn;\n}\nEXPORT_SYMBOL(clk_debug_measure_add);",
        1,
    )

    warn_clk = '\tWARN_CLK(hw->core, clk_hw_get_name(hw), calltrace, "");'
    if warn_clk not in text:
        raise SystemExit("qcom_clk_dump WARN_CLK marker not found")
    text = text.replace(
        warn_clk,
        "\t/* The downstream recursive register dumper is not part of GKI. */\n"
        "\t(void)calltrace;",
        1,
    )

    if "msm_bus_scale_client_update_request" in text or "msm-bus.h" in text:
        raise SystemExit("MSM bus dependency remains in clk-debug.c")
    if "WARN_CLK(" in text:
        raise SystemExit("downstream WARN_CLK dependency remains in clk-debug.c")
    path.write_text(text)


def adapt_debugcc_dummy_clocks(path: Path) -> None:
    text = path.read_text(errors="replace")
    text = text.replace("struct clk_dummy ", "struct lagoon_clk_dummy ")
    text = text.replace("\t.rrate =", "\t.rate =")
    text = text.replace("&clk_dummy_ops", "&lagoon_clk_dummy_ops")

    marker = "static struct lagoon_clk_dummy l3_clk = {"
    if marker not in text:
        raise SystemExit("Lagoon debug dummy-clock marker not found")
    block = (
        "struct lagoon_clk_dummy {\n"
        "\tstruct clk_hw hw;\n"
        "\tunsigned long rate;\n"
        "};\n\n"
        "#define to_lagoon_clk_dummy(_hw) \\\n"
        "\tcontainer_of((_hw), struct lagoon_clk_dummy, hw)\n\n"
        "static unsigned long lagoon_clk_dummy_recalc_rate(\n"
        "\t\tstruct clk_hw *hw, unsigned long parent_rate)\n"
        "{\n"
        "\t(void)parent_rate;\n"
        "\treturn to_lagoon_clk_dummy(hw)->rate;\n"
        "}\n\n"
        "static const struct clk_ops lagoon_clk_dummy_ops = {\n"
        "\t.recalc_rate = lagoon_clk_dummy_recalc_rate,\n"
        "};\n\n"
    )
    text = text.replace(marker, block + marker, 1)
    if "struct clk_dummy" in text or "&clk_dummy_ops" in text or ".rrate =" in text:
        raise SystemExit("downstream dummy-clock API remains in debugcc-lagoon.c")
    path.write_text(text)


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    artifact = args.output.resolve()

    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")
    for required in (
        "drivers/clk/qcom/gcc-lagoon.c",
        "drivers/clk/qcom/camcc-lagoon.c",
        "drivers/clk/qcom/dispcc-lagoon.c",
        "drivers/clk/qcom/gpucc-lagoon.c",
        "drivers/clk/qcom/videocc-lagoon.c",
    ):
        if not (gki / required).is_file():
            raise SystemExit(f"completed earlier clock phase is missing: {required}")

    artifact.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    all_paths = [entry[0] for entry in PROBES.values()] + list(COPY_ONLY)

    for relative in all_paths:
        source = touchgrass / relative
        destination = gki / relative
        if not source.is_file():
            raise SystemExit(f"missing touchGrass source: {relative}")
        before = sha256(destination) if destination.is_file() else "<absent>"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

        if relative == "drivers/clk/qcom/npucc-lagoon.c":
            strip_npu_compat(destination)
            preserve_npu_crc_calibration(destination)
        elif relative == "drivers/clk/qcom/clk-debug.h":
            adapt_clk_debug_header(destination)
        elif relative == "drivers/clk/qcom/clk-debug.c":
            adapt_clk_debug_core(destination)
        elif relative == "drivers/clk/qcom/debugcc-lagoon.c":
            adapt_debugcc_dummy_clocks(destination)

        rows.append({
            "relative_path": relative,
            "purpose": next(
                (description for source_path, _target, _config, description in PROBES.values()
                 if source_path == relative),
                "supporting source or binding",
            ),
            "source_sha256": sha256(source),
            "gki_before_sha256": before,
            "gki_after_sha256": sha256(destination),
        })

    append_once(gki / "drivers/clk/qcom/Makefile", "npucc-lagoon.o",
                "obj-$(CONFIG_NPU_CC_LAGOON) += npucc-lagoon.o")
    append_once(gki / "drivers/clk/qcom/Makefile", "clk-debug.o",
                "obj-$(CONFIG_QCOM_CLK_DEBUG) += clk-debug.o")
    append_once(gki / "drivers/clk/qcom/Makefile", "debugcc-lagoon.o",
                "obj-$(CONFIG_DEBUG_CC_LAGOON) += debugcc-lagoon.o")

    append_once(
        gki / "drivers/clk/qcom/Kconfig",
        "config NPU_CC_LAGOON",
        """config NPU_CC_LAGOON
\ttristate \"Lagoon NPU clock controller\"
\tdepends on COMMON_CLK_QCOM
\tselect QCOM_GDSC
\thelp
\t  Non-flashable A52xq GKI 5.10 NPU clock compile probe.

config QCOM_CLK_DEBUG
\ttristate \"Qualcomm debug clock measurement core\"
\tdepends on COMMON_CLK_QCOM && DEBUG_FS
\thelp
\t  Non-flashable compile probe for Qualcomm clock measurement support.

config DEBUG_CC_LAGOON
\ttristate \"Lagoon debug clock controller\"
\tdepends on QCOM_CLK_DEBUG
\thelp
\t  Non-flashable A52xq GKI 5.10 debug clock compile probe.""",
    )

    fields = ["relative_path", "purpose", "source_sha256", "gki_before_sha256", "gki_after_sha256"]
    write_tsv(artifact / "staged-files.tsv", fields, rows)
    (artifact / "lagoon-remaining-clocks.fragment").write_text(
        "# Lagoon remaining-clock compile probes\n"
        "CONFIG_DEBUG_FS=y\n"
        "CONFIG_NPU_CC_LAGOON=y\n"
        "CONFIG_QCOM_CLK_DEBUG=y\n"
        "CONFIG_DEBUG_CC_LAGOON=y\n"
    )

    subprocess.run(["git", "-C", str(gki), "add", "-N", "--", *all_paths], check=True)
    patch = output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    if not patch:
        raise SystemExit("remaining-clock staging produced no GKI diff")
    (artifact / "lagoon-remaining-clocks-port.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-lagoon-remaining-clocks-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"touchgrass_commit={tg_head}",
        f"planned_probes={len(PROBES)}",
        "phase_dependencies=gcc,pinctrl,llcc,camcc,dispcc,gpucc,videocc",
        "npu_crc_calibration=preserved-as-explicit-regmap-writes",
        "debug_bus_scaling=disabled-for-gki-compile-probe",
        "debug_dummy_clocks=local-fixed-rate-compatibility",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    lines = path.read_text(errors="replace").splitlines()
    patterns = (
        "error:", "fatal error:", "undefined reference", "No rule to make target",
        "No such file or directory", "implicit declaration",
    )
    selected: list[str] = []
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= 12:
            break
    if not selected:
        selected = [line.strip() for line in lines[-10:] if line.strip()]
    return selected or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe") for row in rows} != set(PROBES):
        raise SystemExit("remaining-clock compile status probe set mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    passed = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    report = [
        "# A52xq GKI 5.10 Lagoon remaining clocks probe", "", "## Result", "",
        f"- compiled: **{passed}**", f"- compile failures: **{failed}**",
        f"- Kconfig blocked: **{blocked}**", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "",
            f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
            f"- result: **{row['result']}**",
            f"- object produced: `{row['object_produced']}`", "", "First diagnostics:", "",
        ])
        report.extend(f"- `{line.replace('`', chr(39))}`" for line in diagnostics(artifact / "logs" / f"{probe}.log"))
        report.append("")
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([f"compiled_success={passed}", f"compile_failed={failed}", f"config_blocked={blocked}"])
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(path for path in artifact.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--gki", type=Path, required=True)
    stage_parser.add_argument("--touchgrass", type=Path, required=True)
    stage_parser.add_argument("--output", type=Path, required=True)
    stage_parser.set_defaults(func=stage)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--status-file", type=Path, required=True)
    finalize_parser.set_defaults(func=finalize)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
