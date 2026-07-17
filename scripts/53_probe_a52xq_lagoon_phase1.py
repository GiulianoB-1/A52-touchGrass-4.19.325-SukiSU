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

STAGED_FILES = [
    ("binding", "include/dt-bindings/clock/qcom,gcc-lagoon.h", "GCC clock and reset IDs"),
    ("binding", "include/dt-bindings/clock/qcom,camcc-lagoon.h", "camera clock IDs"),
    ("binding", "include/dt-bindings/clock/qcom,dispcc-lagoon.h", "display clock IDs"),
    ("binding", "include/dt-bindings/clock/qcom,gpucc-lagoon.h", "GPU clock IDs"),
    ("binding", "include/dt-bindings/clock/qcom,videocc-lagoon.h", "video clock IDs"),
    ("binding", "include/dt-bindings/phy/qcom,lagoon-qmp-usb3.h", "USB3 PHY IDs"),
    ("driver", "drivers/clk/qcom/gcc-lagoon.c", "Lagoon global clock controller"),
    ("driver", "drivers/pinctrl/qcom/pinctrl-lagoon.c", "Lagoon TLMM pin controller"),
]

PROBES = {
    "gcc-lagoon": ("drivers/clk/qcom/gcc-lagoon.o", "CONFIG_SDM_GCC_LAGOON"),
    "pinctrl-lagoon": ("drivers/pinctrl/qcom/pinctrl-lagoon.o", "CONFIG_PINCTRL_LAGOON"),
    "llcc-lagoon": ("drivers/soc/qcom/llcc-qcom.o", "CONFIG_QCOM_LLCC"),
}

CLOCK_KCONFIG = '''
config SDM_GCC_LAGOON
\ttristate "LAGOON Global Clock Controller"
\tdepends on COMMON_CLK_QCOM
\tselect QCOM_GDSC
\thelp
\t  Compile-probe support for the Qualcomm Lagoon global clock controller.
'''.strip()

PINCTRL_KCONFIG = '''
config PINCTRL_LAGOON
\ttristate "Qualcomm Lagoon pin controller driver"
\tdepends on GPIOLIB && OF
\tselect PINCTRL_MSM
\thelp
\t  Compile-probe support for the Qualcomm Lagoon TLMM pin controller.
'''.strip()

CONFIG_FRAGMENT = [
    "# A52xq Lagoon phase-1 compile-probe fragment",
    "# Non-flashable bring-up input.",
    "CONFIG_SDM_GCC_LAGOON=y",
    "CONFIG_PINCTRL_LAGOON=y",
    "CONFIG_QCOM_LLCC=y",
]

LAGOON_LLCC_DATA = '''
/* A52xq Lagoon LLCC data ported from the downstream Linux 4.19 driver. */
static const struct llcc_slice_config lagoon_data[] = {
\t{ LLCC_CPUSS,  1, 768, 1, 0, 0xfff, 0, 0, 0, 1, 1, 0 },
\t{ LLCC_MDM,    8, 512, 2, 0, 0xfff, 0, 0, 0, 1, 0, 0 },
\t{ LLCC_GPUHTW, 11, 256, 1, 0, 0xfff, 0, 0, 0, 1, 0, 0 },
\t{ LLCC_GPU,    12, 512, 1, 0, 0xfff, 0, 0, 0, 1, 0, 0 },
\t{ LLCC_MDMPNG, 21, 768, 0, 1, 0xfff, 0, 0, 0, 1, 0, 0 },
\t{ 23,          23, 768, 1, 0, 0xfff, 0, 0, 0, 1, 0, 0 }, /* LLCC_NPU */
\t{ 29,          29,  64, 1, 1, 0xfff, 0, 0, 0, 1, 0, 0 }, /* LLCC_MODEMVPE */
};

static const struct qcom_llcc_config lagoon_cfg = {
\t.sct_data = lagoon_data,
\t.size = ARRAY_SIZE(lagoon_data),
};
'''.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(errors="replace")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n")


def insert_before_last(path: Path, token: str, marker: str, block: str) -> None:
    text = path.read_text(errors="replace")
    if marker in text:
        return
    matches = list(re.finditer(rf"(?m)^\s*{re.escape(token)}\s*$", text))
    if not matches:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n")
        return
    index = matches[-1].start()
    path.write_text(text[:index].rstrip() + "\n\n" + block + "\n\n" + text[index:])


def adapt_gcc_driver(path: Path) -> None:
    lines = path.read_text(errors="replace").splitlines()
    output: list[str] = []
    removed_rates = 0
    removed_regulators = 0
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped == '#include "vdd-level-lagoon.h"' or "DEFINE_VDD_REGULATORS(" in line:
            index += 1
            continue
        if ".enable_safe_config =" in line:
            index += 1
            continue
        if "&clk_branch2_hw_ctl_ops" in line:
            output.append(line.replace("&clk_branch2_hw_ctl_ops", "&clk_branch2_ops"))
            index += 1
            continue

        if ".vdd_class =" in line:
            index += 1
            if index < len(lines) and ".num_rate_max =" in lines[index]:
                index += 1
            if index >= len(lines) or ".rate_max =" not in lines[index]:
                raise SystemExit("downstream GCC VDD rate table is malformed")
            depth = lines[index].count("{") - lines[index].count("}")
            index += 1
            while index < len(lines) and depth > 0:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            removed_rates += 1
            continue

        if re.match(r"^vdd_cx(?:_ao)?\.regulator\[0\] = devm_regulator_get", stripped):
            index += 1
            if index >= len(lines) or not lines[index].lstrip().startswith("if (IS_ERR("):
                raise SystemExit("downstream GCC regulator block is malformed")
            depth = lines[index].count("{") - lines[index].count("}")
            index += 1
            while index < len(lines) and depth > 0:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            removed_regulators += 1
            continue

        output.append(line)
        index += 1

    if removed_rates < 2 or removed_regulators != 2:
        raise SystemExit(
            f"unexpected GCC VDD adaptation counts: rates={removed_rates}, regulators={removed_regulators}"
        )

    text = "\n".join(output) + "\n"
    forbidden = (
        "DEFINE_VDD_REGULATORS", ".vdd_class =", ".num_rate_max =", ".rate_max =",
        "vdd_cx.regulator", "vdd_cx_ao.regulator", '"vdd-level-lagoon.h"',
        ".enable_safe_config =", "clk_branch2_hw_ctl_ops",
    )
    leftovers = [item for item in forbidden if item in text]
    if leftovers:
        raise SystemExit(f"unsupported GCC tokens remain: {leftovers}")
    path.write_text(text)


def adapt_pinctrl_driver(path: Path) -> None:
    text = path.read_text(errors="replace")
    unsupported = (
        ".dir_conn_reg =", ".egpio_enable =", ".egpio_present =",
        ".dir_conn_en_bit =", ".wake_reg =", ".wake_bit =", ".dir_conn =",
    )
    text = "\n".join(
        line for line in text.splitlines()
        if not any(field in line for field in unsupported)
    ) + "\n"
    text, count = re.subn(
        r"\nstatic struct msm_dir_conn lagoon_dir_conn\[\] = \{.*?\n\};\n",
        "\n", text, flags=re.DOTALL,
    )
    if count != 1:
        raise SystemExit("expected one downstream msm_dir_conn table")
    path.write_text(text)


def integrate_llcc(path: Path) -> None:
    text = path.read_text(errors="replace")
    if "static const struct llcc_slice_config lagoon_data[]" not in text:
        anchor = "static const struct qcom_llcc_config sc7180_cfg = {"
        if anchor not in text:
            raise SystemExit("GKI LLCC configuration anchor is missing")
        text = text.replace(anchor, LAGOON_LLCC_DATA + "\n\n" + anchor, 1)

    match = '{ .compatible = "lagoon-llcc-v1", .data = &lagoon_cfg },'
    if match not in text:
        anchor = "static const struct of_device_id qcom_llcc_of_match[] = {"
        if anchor not in text:
            raise SystemExit("GKI LLCC match-table anchor is missing")
        text = text.replace(anchor, anchor + "\n\t" + match, 1)
    path.write_text(text)


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    output = args.output.resolve()
    seed = args.seed_config.resolve()

    gki_head = command_output("git", "-C", str(gki), "rev-parse", "HEAD")
    tg_head = command_output("git", "-C", str(touchgrass), "rev-parse", "HEAD")
    if gki_head != GKI_SHA or tg_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected source revisions: gki={gki_head}, touchgrass={tg_head}")
    if not seed.is_file():
        raise SystemExit(f"missing Workflow 52 resolved config: {seed}")

    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for kind, relative, purpose in STAGED_FILES:
        source = touchgrass / relative
        target = gki / relative
        if not source.is_file():
            raise SystemExit(f"missing touchGrass source: {relative}")
        before = sha256(target) if target.is_file() else "<absent>"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if relative == "drivers/clk/qcom/gcc-lagoon.c":
            adapt_gcc_driver(target)
        elif relative == "drivers/pinctrl/qcom/pinctrl-lagoon.c":
            adapt_pinctrl_driver(target)
        rows.append({
            "kind": kind, "relative_path": relative, "purpose": purpose,
            "source_sha256": sha256(source), "gki_before_sha256": before,
            "gki_after_sha256": sha256(target),
        })

    llcc_source = touchgrass / "drivers/soc/qcom/llcc-lagoon.c"
    llcc_target = gki / "drivers/soc/qcom/llcc-qcom.c"
    if not llcc_source.is_file():
        raise SystemExit("missing downstream Lagoon LLCC source")
    before = sha256(llcc_target)
    integrate_llcc(llcc_target)
    rows.append({
        "kind": "integration", "relative_path": "drivers/soc/qcom/llcc-qcom.c",
        "purpose": "Lagoon LLCC slice data integrated into the GKI core driver",
        "source_sha256": sha256(llcc_source), "gki_before_sha256": before,
        "gki_after_sha256": sha256(llcc_target),
    })

    append_once(gki / "drivers/clk/qcom/Kconfig", "config SDM_GCC_LAGOON", CLOCK_KCONFIG)
    append_once(
        gki / "drivers/clk/qcom/Makefile", "gcc-lagoon.o",
        "obj-$(CONFIG_SDM_GCC_LAGOON) += gcc-lagoon.o",
    )
    insert_before_last(
        gki / "drivers/pinctrl/qcom/Kconfig", "endif", "config PINCTRL_LAGOON",
        PINCTRL_KCONFIG,
    )
    append_once(
        gki / "drivers/pinctrl/qcom/Makefile", "pinctrl-lagoon.o",
        "obj-$(CONFIG_PINCTRL_LAGOON) += pinctrl-lagoon.o",
    )

    (output / "lagoon-phase1.fragment").write_text("\n".join(CONFIG_FRAGMENT) + "\n")
    shutil.copy2(seed, output / "workflow52-resolved.config")
    fields = [
        "kind", "relative_path", "purpose", "source_sha256",
        "gki_before_sha256", "gki_after_sha256",
    ]
    write_tsv(output / "staged-files.tsv", fields, rows)

    new_paths = [relative for _, relative, _ in STAGED_FILES]
    subprocess.run(["git", "-C", str(gki), "add", "-N", "--", *new_paths], check=True)
    patch = command_output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    if not patch:
        raise SystemExit("staging produced no GKI diff")
    (output / "lagoon-phase1-port.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-lagoon-phase1-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"gki_kernel_version={command_output('make', '-s', '-C', str(gki), 'kernelversion')}",
        f"touchgrass_commit={tg_head}",
        f"touchgrass_kernel_version={command_output('make', '-s', '-C', str(touchgrass), 'kernelversion')}",
        f"staged_files={len(rows)}", f"planned_probes={len(PROBES)}",
        "llcc_integration=drivers/soc/qcom/llcc-qcom.c",
    ]
    (output / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def first_diagnostics(log: Path) -> list[str]:
    if not log.is_file():
        return ["log missing"]
    lines = log.read_text(errors="replace").splitlines()
    patterns = ("error:", "fatal error:", "undefined reference", "No rule to make target", "No such file or directory")
    selected: list[str] = []
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= 8:
            break
    if not selected:
        selected = [line.strip() for line in lines[-8:] if line.strip()]
    return selected or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    status = args.status_file.resolve()
    if not status.is_file():
        raise SystemExit(f"missing compile status: {status}")
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe", "") for row in rows} != set(PROBES):
        raise SystemExit("compile status probe set does not match Workflow 53")
    shutil.copy2(status, output / "compile-status.tsv")

    passed = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    report = [
        "# A52xq GKI 5.10 Lagoon phase-1 compile probe", "", "## Result", "",
        f"- probes compiled successfully: **{passed}**",
        f"- probes with compiler/API failures: **{failed}**",
        f"- probes blocked by Kconfig resolution: **{blocked}**", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "", f"- target: `{row['target']}`",
            f"- requested symbol: `{row['config_symbol']}`",
            f"- resolved value: `{row['resolved_value']}`",
            f"- result: **{row['result']}**", f"- compiler exit code: `{row['exit_code']}`",
            f"- object produced: `{row['object_produced']}`", "", "First diagnostics:", "",
        ])
        report.extend(f"- `{line.replace('`', chr(39))}`" for line in first_diagnostics(output / "logs" / f"{probe}.log"))
        report.append("")
    report.extend(["## Next gate", "", "Add the next Lagoon platform layer only after all three phase-1 objects compile.", ""])
    (output / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (output / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([f"compiled_success={passed}", f"compile_failed={failed}", f"config_blocked={blocked}"])
    (output / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    with (output / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--gki", type=Path, required=True)
    stage_parser.add_argument("--touchgrass", type=Path, required=True)
    stage_parser.add_argument("--seed-config", type=Path, required=True)
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
