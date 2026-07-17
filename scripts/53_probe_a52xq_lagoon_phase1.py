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
    "gcc-lagoon": {
        "target": "drivers/clk/qcom/gcc-lagoon.o",
        "symbol": "CONFIG_SDM_GCC_LAGOON",
    },
    "pinctrl-lagoon": {
        "target": "drivers/pinctrl/qcom/pinctrl-lagoon.o",
        "symbol": "CONFIG_PINCTRL_LAGOON",
    },
    "llcc-lagoon": {
        "target": "drivers/soc/qcom/llcc-qcom.o",
        "symbol": "CONFIG_QCOM_LLCC",
    },
}

CLOCK_KCONFIG = '''
config SDM_GCC_LAGOON
\ttristate "LAGOON Global Clock Controller"
\tdepends on COMMON_CLK_QCOM
\tselect QCOM_GDSC
\thelp
\t  Compile-probe support for the Qualcomm Lagoon global clock controller.
\t  This entry is part of the A52xq 5.10 bring-up tree and is not upstream-ready.
'''.strip()

PINCTRL_KCONFIG = '''
config PINCTRL_LAGOON
\ttristate "Qualcomm Lagoon pin controller driver"
\tdepends on GPIOLIB && OF
\tselect PINCTRL_MSM
\thelp
\t  Compile-probe support for the Qualcomm Lagoon TLMM pin controller.
\t  This entry is part of the A52xq 5.10 bring-up tree and is not upstream-ready.
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
\t{
\t\t.usecase_id = LLCC_CPUSS,
\t\t.slice_id = 1,
\t\t.max_cap = 768,
\t\t.priority = 1,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t\t.activate_on_init = true,
\t}, {
\t\t.usecase_id = LLCC_MDM,
\t\t.slice_id = 8,
\t\t.max_cap = 512,
\t\t.priority = 2,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t}, {
\t\t.usecase_id = LLCC_GPUHTW,
\t\t.slice_id = 11,
\t\t.max_cap = 256,
\t\t.priority = 1,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t}, {
\t\t.usecase_id = LLCC_GPU,
\t\t.slice_id = 12,
\t\t.max_cap = 512,
\t\t.priority = 1,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t}, {
\t\t.usecase_id = LLCC_MDMPNG,
\t\t.slice_id = 21,
\t\t.max_cap = 768,
\t\t.fixed_size = true,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t}, {
\t\t.usecase_id = 23, /* downstream LLCC_NPU */
\t\t.slice_id = 23,
\t\t.max_cap = 768,
\t\t.priority = 1,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t}, {
\t\t.usecase_id = 29, /* downstream LLCC_MODEMVPE */
\t\t.slice_id = 29,
\t\t.max_cap = 64,
\t\t.priority = 1,
\t\t.fixed_size = true,
\t\t.bonus_ways = 0xfff,
\t\t.retain_on_pc = true,
\t},
};

static const u32 lagoon_reg_offset[] = {
\t[LLCC_COMMON_HW_INFO] = 0x00030000,
\t[LLCC_COMMON_STATUS0] = 0x0003000c,
};

static const struct qcom_llcc_config lagoon_cfg = {
\t.sct_data = lagoon_data,
\t.size = ARRAY_SIZE(lagoon_data),
\t.need_llcc_cfg = true,
\t.reg_offset = lagoon_reg_offset,
};
'''.strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(tree: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(tree), "rev-parse", "HEAD"], text=True
    ).strip()


def kernel_version(tree: Path) -> str:
    return subprocess.check_output(
        ["make", "-s", "-C", str(tree), "kernelversion"], text=True
    ).strip()


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(errors="replace")
    if marker in text:
        return
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
    path.write_text(text[:index].rstrip() + "\n\n" + block.rstrip() + "\n\n" + text[index:])


def adapt_gcc_driver(path: Path) -> None:
    lines = path.read_text(errors="replace").splitlines()
    out: list[str] = []
    removed_rate_tables = 0
    removed_regulators = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == '#include "vdd-level-lagoon.h"':
            i += 1
            continue

        if "DEFINE_VDD_REGULATORS(" in line:
            i += 1
            continue

        if ".enable_safe_config =" in line:
            i += 1
            continue

        if "&clk_branch2_hw_ctl_ops" in line:
            out.append(line.replace("&clk_branch2_hw_ctl_ops", "&clk_branch2_ops"))
            i += 1
            continue

        if ".vdd_class =" in line:
            i += 1
            if i < len(lines) and ".num_rate_max =" in lines[i]:
                i += 1
            if i >= len(lines) or ".rate_max =" not in lines[i]:
                raise SystemExit("downstream GCC VDD block is missing rate_max")

            depth = lines[i].count("{") - lines[i].count("}")
            i += 1
            while i < len(lines) and depth > 0:
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            if depth != 0:
                raise SystemExit("unterminated downstream GCC rate_max block")
            removed_rate_tables += 1
            continue

        if re.match(r"^vdd_cx(?:_ao)?\.regulator\[0\] = devm_regulator_get", stripped):
            i += 1
            if i >= len(lines) or not lines[i].lstrip().startswith("if (IS_ERR("):
                raise SystemExit("downstream GCC regulator assignment has no error block")

            depth = lines[i].count("{") - lines[i].count("}")
            i += 1
            while i < len(lines) and depth > 0:
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            if depth != 0:
                raise SystemExit("unterminated downstream GCC regulator block")
            removed_regulators += 1
            continue

        out.append(line)
        i += 1

    if removed_rate_tables < 2:
        raise SystemExit("expected downstream GCC VDD rate tables")
    if removed_regulators != 2:
        raise SystemExit(
            f"expected two downstream GCC regulator blocks, removed {removed_regulators}"
        )

    text = "\n".join(out) + "\n"
    forbidden = (
        "DEFINE_VDD_REGULATORS",
        ".vdd_class =",
        ".num_rate_max =",
        ".rate_max =",
        "vdd_cx.regulator",
        "vdd_cx_ao.regulator",
        '"vdd-level-lagoon.h"',
        ".enable_safe_config =",
        "clk_branch2_hw_ctl_ops",
    )
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f"GCC adaptation left unsupported tokens: {leftovers}")
    path.write_text(text)


def adapt_pinctrl_driver(path: Path) -> None:
    text = path.read_text(errors="replace")
    unsupported_fields = (
        ".dir_conn_reg =",
        ".egpio_enable =",
        ".egpio_present =",
        ".dir_conn_en_bit =",
        ".wake_reg =",
        ".wake_bit =",
        ".dir_conn =",
    )
    lines = [
        line for line in text.splitlines()
        if not any(field in line for field in unsupported_fields)
    ]
    text = "\n".join(lines) + "\n"
    text, count = re.subn(
        r"\nstatic struct msm_dir_conn lagoon_dir_conn\[\] = \{.*?\n\};\n",
        "\n",
        text,
        flags=re.DOTALL,
    )
    if count != 1:
        raise SystemExit("expected exactly one downstream msm_dir_conn table")
    path.write_text(text)


def integrate_llcc(path: Path) -> None:
    text = path.read_text(errors="replace")
    if "static const struct llcc_slice_config lagoon_data[]" not in text:
        anchor = "static const struct qcom_llcc_config sc7180_cfg = {"
        if anchor not in text:
            raise SystemExit("could not locate the first GKI LLCC configuration")
        text = text.replace(anchor, LAGOON_LLCC_DATA + "\n\n" + anchor, 1)

    match = '{ .compatible = "lagoon-llcc-v1", .data = &lagoon_cfg },'
    if match not in text:
        anchor = "static const struct of_device_id qcom_llcc_of_match[] = {"
        if anchor not in text:
            raise SystemExit("could not locate the GKI LLCC OF match table")
        text = text.replace(anchor, anchor + "\n\t" + match, 1)

    path.write_text(text)


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    touchgrass = args.touchgrass.resolve()
    out = args.output.resolve()
    seed_config = args.seed_config.resolve()

    gki_head = git_head(gki)
    touchgrass_head = git_head(touchgrass)
    if gki_head != GKI_SHA:
        raise SystemExit(f"unexpected GKI commit: {gki_head}")
    if touchgrass_head != TOUCHGRASS_SHA:
        raise SystemExit(f"unexpected touchGrass commit: {touchgrass_head}")
    if not seed_config.is_file():
        raise SystemExit(f"missing Workflow 52 resolved config: {seed_config}")

    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for kind, rel, purpose in STAGED_FILES:
        src = touchgrass / rel
        dst = gki / rel
        if not src.is_file():
            raise SystemExit(f"missing touchGrass source: {rel}")
        before = sha256(dst) if dst.is_file() else "<absent>"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if rel == "drivers/clk/qcom/gcc-lagoon.c":
            adapt_gcc_driver(dst)
        elif rel == "drivers/pinctrl/qcom/pinctrl-lagoon.c":
            adapt_pinctrl_driver(dst)
        rows.append(
            {
                "kind": kind,
                "relative_path": rel,
                "purpose": purpose,
                "source_sha256": sha256(src),
                "gki_before_sha256": before,
                "gki_after_sha256": sha256(dst),
            }
        )

    llcc_source = touchgrass / "drivers/soc/qcom/llcc-lagoon.c"
    llcc_target = gki / "drivers/soc/qcom/llcc-qcom.c"
    if not llcc_source.is_file():
        raise SystemExit("missing downstream Lagoon LLCC source")
    llcc_before = sha256(llcc_target)
    integrate_llcc(llcc_target)
    rows.append(
        {
            "kind": "integration",
            "relative_path": "drivers/soc/qcom/llcc-qcom.c",
            "purpose": "Lagoon LLCC slice data integrated into the GKI core driver",
            "source_sha256": sha256(llcc_source),
            "gki_before_sha256": llcc_before,
            "gki_after_sha256": sha256(llcc_target),
        }
    )

    append_once(
        gki / "drivers/clk/qcom/Kconfig",
        "config SDM_GCC_LAGOON",
        CLOCK_KCONFIG,
    )
    append_once(
        gki / "drivers/clk/qcom/Makefile",
        "gcc-lagoon.o",
        "obj-$(CONFIG_SDM_GCC_LAGOON) += gcc-lagoon.o",
    )
    insert_before_last(
        gki / "drivers/pinctrl/qcom/Kconfig",
        "endif",
        "config PINCTRL_LAGOON",
        PINCTRL_KCONFIG,
    )
    append_once(
        gki / "drivers/pinctrl/qcom/Makefile",
        "pinctrl-lagoon.o",
        "obj-$(CONFIG_PINCTRL_LAGOON) += pinctrl-lagoon.o",
    )

    (out / "lagoon-phase1.fragment").write_text("\n".join(CONFIG_FRAGMENT) + "\n")
    shutil.copy2(seed_config, out / "workflow52-resolved.config")
    write_tsv(
        out / "staged-files.tsv",
        [
            "kind",
            "relative_path",
            "purpose",
            "source_sha256",
            "gki_before_sha256",
            "gki_after_sha256",
        ],
        rows,
    )

    new_paths = [rel for _, rel, _ in STAGED_FILES]
    subprocess.run(
        ["git", "-C", str(gki), "add", "-N", "--", *new_paths],
        check=True,
    )
    patch = subprocess.check_output(
        ["git", "-C", str(gki), "diff", "--binary", "--no-ext-diff"], text=True
    )
    (out / "lagoon-phase1-port.patch").write_text(patch)
    if not patch.strip():
        raise SystemExit("staging produced no GKI diff")

    metadata = [
        "artifact_type=a52xq-gki-5.10-lagoon-phase1-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"gki_kernel_version={kernel_version(gki)}",
        f"touchgrass_commit={touchgrass_head}",
        f"touchgrass_kernel_version={kernel_version(touchgrass)}",
        f"staged_files={len(rows)}",
        f"planned_probes={len(PROBES)}",
        "llcc_integration=drivers/soc/qcom/llcc-qcom.c",
    ]
    (out / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def first_diagnostics(log: Path) -> list[str]:
    if not log.is_file():
        return ["log missing"]
    lines = log.read_text(errors="replace").splitlines()
    patterns = (
        "error:",
        "fatal error:",
        "undefined reference",
        "No rule to make target",
        "No such file or directory",
    )
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
    out = args.output.resolve()
    status_path = args.status_file.resolve()
    if not status_path.is_file():
        raise SystemExit(f"missing compile status: {status_path}")

    with status_path.open(newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    expected = set(PROBES)
    found = {row.get("probe", "") for row in rows}
    if found != expected:
        raise SystemExit(f"compile status probes mismatch: expected {sorted(expected)}, found {sorted(found)}")

    shutil.copy2(status_path, out / "compile-status.tsv")

    passed = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)

    report = [
        "# A52xq GKI 5.10 Lagoon phase-1 compile probe",
        "",
        "This artifact is a non-flashable source-port probe. It stages the first Lagoon platform files in the pinned Android 12 GKI 5.10 tree and compiles each platform object independently.",
        "",
        "## Result",
        "",
        f"- probes compiled successfully: **{passed}**",
        f"- probes with compiler/API failures: **{failed}**",
        f"- probes blocked by Kconfig resolution: **{blocked}**",
        "",
        "A compiler failure here is an expected porting result, not a device boot result. The logs identify the first Linux 4.19 to 5.10 API adaptations required.",
        "",
        "## Probe details",
        "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend(
            [
                f"### `{probe}`",
                "",
                f"- target: `{row['target']}`",
                f"- requested symbol: `{row['config_symbol']}`",
                f"- resolved value: `{row['resolved_value']}`",
                f"- result: **{row['result']}**",
                f"- compiler exit code: `{row['exit_code']}`",
                f"- object produced: `{row['object_produced']}`",
                "",
                "First diagnostics:",
                "",
            ]
        )
        for line in first_diagnostics(out / "logs" / f"{probe}.log"):
            report.append(f"- `{line.replace('`', chr(39))}`")
        report.append("")

    report.extend(
        [
            "## Next gate",
            "",
            "Adapt only the compile failures in these three drivers. Do not add the Lagoon device tree or build a flashable kernel until all three objects compile in the pinned 5.10 tree.",
            "",
        ]
    )
    (out / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata_path = out / "analysis-metadata.txt"
    metadata = metadata_path.read_text().rstrip().splitlines()
    metadata.extend(
        [
            f"compiled_success={passed}",
            f"compile_failed={failed}",
            f"config_blocked={blocked}",
        ]
    )
    metadata_path.write_text("\n".join(metadata) + "\n")

    sums = out / "SHA256SUMS"
    files = sorted(
        p for p in out.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS"
    )
    with sums.open("w") as f:
        for path in files:
            f.write(f"{sha256(path)}  {path.relative_to(out).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_stage = sub.add_parser("stage")
    p_stage.add_argument("--gki", type=Path, required=True)
    p_stage.add_argument("--touchgrass", type=Path, required=True)
    p_stage.add_argument("--seed-config", type=Path, required=True)
    p_stage.add_argument("--output", type=Path, required=True)
    p_stage.set_defaults(func=stage)

    p_finalize = sub.add_parser("finalize")
    p_finalize.add_argument("--output", type=Path, required=True)
    p_finalize.add_argument("--status-file", type=Path, required=True)
    p_finalize.set_defaults(func=finalize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
