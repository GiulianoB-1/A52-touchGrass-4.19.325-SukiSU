#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable


def sh(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()


def strip_c_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def normalize_c(s: str) -> str:
    s = strip_c_comments(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def resolve(root: Path, candidates: Iterable[str], basename_hint: str | None = None) -> Path | None:
    for rel in candidates:
        p = root / rel
        if p.is_file():
            return p
    if basename_hint:
        hits = list(root.rglob(basename_hint))
        if len(hits) == 1:
            return hits[0]
        # Prefer canonical kernel locations over generated/tools copies.
        hits.sort(key=lambda p: ("tools/" in str(p), len(p.parts), str(p)))
        if hits:
            return hits[0]
    return None


def rel(root: Path, p: Path | None) -> str:
    if p is None:
        return "-"
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def read(p: Path | None) -> str:
    if p is None:
        return ""
    return p.read_text(errors="replace")


def extract_function(text: str, name: str) -> str | None:
    # Find a definition, rejecting prototypes by requiring '{' before ';'.
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", text):
        i = m.start()
        j = m.end()
        depth = 1
        in_str = None
        esc = False
        while j < len(text) and depth:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == in_str:
                    in_str = None
            else:
                if c in "\"'":
                    in_str = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
            j += 1
        if depth:
            continue
        k = j
        while k < len(text) and text[k] not in "{;":
            k += 1
        if k >= len(text) or text[k] != "{":
            continue
        # Include the signature line(s) for useful diffs.
        start = text.rfind("\n", 0, i) + 1
        b = k
        brace = 0
        in_str = None
        esc = False
        in_line = False
        in_block = False
        while b < len(text):
            c = text[b]
            n = text[b + 1] if b + 1 < len(text) else ""
            if in_line:
                if c == "\n":
                    in_line = False
            elif in_block:
                if c == "*" and n == "/":
                    in_block = False
                    b += 1
            elif in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == in_str:
                    in_str = None
            else:
                if c == "/" and n == "/":
                    in_line = True
                    b += 1
                elif c == "/" and n == "*":
                    in_block = True
                    b += 1
                elif c in "\"'":
                    in_str = c
                elif c == "{":
                    brace += 1
                elif c == "}":
                    brace -= 1
                    if brace == 0:
                        return text[start:b + 1]
            b += 1
    return None


def parse_config(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path is None or not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if m:
            out[m.group(1)] = m.group(2)
            continue
        m = re.match(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if m:
            out[m.group(1)] = "n"
    return out


def limited_diff(a: str, b: str, fromfile: str, tofile: str, limit: int = 500) -> str:
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), fromfile, tofile, lineterm="", n=3))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... DIFF TRUNCATED: {len(lines)-limit} more lines ..."]
    return "\n".join(lines) + ("\n" if lines else "")


FILE_PAIRS = [
    # label, category, TouchGrass candidates, GKI candidates, basename fallback
    ("stop_machine", "GLOBAL-STOP", ["kernel/stop_machine.c"], ["kernel/stop_machine.c"], "stop_machine.c"),
    ("cpu_hotplug", "CPU-HOTPLUG", ["kernel/cpu.c"], ["kernel/cpu.c"], "cpu.c"),
    ("smp_core", "SMP-IPI", ["kernel/smp.c"], ["kernel/smp.c"], "smp.c"),
    ("arm64_smp", "SMP-IPI", ["arch/arm64/kernel/smp.c"], ["arch/arm64/kernel/smp.c"], "smp.c"),
    ("arm64_suspend", "PSCI-IDLE", ["arch/arm64/kernel/suspend.c"], ["arch/arm64/kernel/suspend.c"], "suspend.c"),
    ("arm64_cpuidle", "PSCI-IDLE", ["arch/arm64/kernel/cpuidle.c"], ["arch/arm64/kernel/cpuidle.c"], "cpuidle.c"),
    ("psci", "PSCI-IDLE", ["drivers/firmware/psci.c", "drivers/firmware/psci/psci.c"], ["drivers/firmware/psci/psci.c", "drivers/firmware/psci.c"], "psci.c"),
    ("cpuidle_core", "PSCI-IDLE", ["drivers/cpuidle/cpuidle.c"], ["drivers/cpuidle/cpuidle.c"], "cpuidle.c"),
    ("cpuidle_driver", "PSCI-IDLE", ["drivers/cpuidle/driver.c"], ["drivers/cpuidle/driver.c"], "driver.c"),
    ("gic_v3", "IRQ-GIC", ["drivers/irqchip/irq-gic-v3.c"], ["drivers/irqchip/irq-gic-v3.c"], "irq-gic-v3.c"),
    ("arch_timer", "IRQ-GIC", ["drivers/clocksource/arm_arch_timer.c"], ["drivers/clocksource/arm_arch_timer.c"], "arm_arch_timer.c"),
    ("component", "DRM-PM", ["drivers/base/component.c"], ["drivers/base/component.c"], "component.c"),
    ("driver_core", "DRM-PM", ["drivers/base/dd.c"], ["drivers/base/dd.c"], "dd.c"),
    ("runtime_pm", "DRM-PM", ["drivers/base/power/runtime.c"], ["drivers/base/power/runtime.c"], "runtime.c"),
    ("genpd", "POWER", ["drivers/base/power/domain.c"], ["drivers/base/power/domain.c"], "domain.c"),
    ("workqueue", "SCHED-WQ", ["kernel/workqueue.c"], ["kernel/workqueue.c"], "workqueue.c"),
    ("sched_core", "SCHED-WQ", ["kernel/sched/core.c"], ["kernel/sched/core.c"], "core.c"),
    ("qcom_scm", "SCM", ["drivers/firmware/qcom_scm.c", "drivers/soc/qcom/scm.c"], ["drivers/firmware/qcom_scm.c", "drivers/firmware/qcom_scm.c"], "qcom_scm.c"),
    ("qcom_scm_smc", "SCM", ["drivers/firmware/qcom_scm-smc.c", "drivers/soc/qcom/scm-smc.c"], ["drivers/firmware/qcom_scm-smc.c"], "qcom_scm-smc.c"),
    ("rpmh", "RPMH-ICC", ["drivers/soc/qcom/rpmh.c"], ["drivers/soc/qcom/rpmh.c"], "rpmh.c"),
    ("rpmh_rsc", "RPMH-ICC", ["drivers/soc/qcom/rpmh-rsc.c"], ["drivers/soc/qcom/rpmh-rsc.c"], "rpmh-rsc.c"),
    ("qcom_icc", "RPMH-ICC", ["drivers/interconnect/qcom/icc-rpmh.c", "drivers/interconnect/qcom/icc-rpm.c"], ["drivers/interconnect/qcom/icc-rpmh.c", "drivers/interconnect/qcom/icc-rpm.c"], "icc-rpmh.c"),
    # Display: vendor TouchGrass techpack vs ported GKI location.
    ("msm_drv", "DISPLAY-PREOPEN", ["drivers/gpu/drm/msm/msm_drv.c", "techpack/display/msm/msm_drv.c"], ["drivers/a52_display/msm/msm_drv.c", "drivers/gpu/drm/msm/msm_drv.c"], "msm_drv.c"),
    ("msm_atomic", "DISPLAY-PREOPEN", ["techpack/display/msm/msm_atomic.c", "drivers/gpu/drm/msm/msm_atomic.c"], ["drivers/a52_display/msm/msm_atomic.c", "drivers/gpu/drm/msm/msm_atomic.c"], "msm_atomic.c"),
    ("sde_kms", "DISPLAY-PREOPEN", ["techpack/display/msm/sde/sde_kms.c", "drivers/gpu/drm/msm/sde/sde_kms.c"], ["drivers/a52_display/msm/sde/sde_kms.c"], "sde_kms.c"),
    ("dsi_display", "DISPLAY-PREOPEN", ["techpack/display/msm/dsi/dsi_display.c"], ["drivers/a52_display/msm/dsi/dsi_display.c"], "dsi_display.c"),
    ("dsi_ctrl", "DISPLAY-PREOPEN", ["techpack/display/msm/dsi/dsi_ctrl.c"], ["drivers/a52_display/msm/dsi/dsi_ctrl.c"], "dsi_ctrl.c"),
    ("dsi_drm", "DISPLAY-PREOPEN", ["techpack/display/msm/dsi/dsi_drm.c"], ["drivers/a52_display/msm/dsi/dsi_drm.c"], "dsi_drm.c"),
]

FUNCTIONS: dict[str, list[str]] = {
    "stop_machine": ["cpu_stop_queue_work", "cpu_stopper_thread", "stop_one_cpu", "stop_cpus", "try_stop_cpus", "multi_cpu_stop", "stop_machine", "stop_machine_from_inactive_cpu"],
    "cpu_hotplug": ["cpu_up", "_cpu_up", "cpu_down", "_cpu_down", "cpuhp_thread_fun", "cpuhp_invoke_callback", "cpuhp_issue_call"],
    "smp_core": ["smp_call_function_many", "smp_call_function_single", "generic_exec_single", "on_each_cpu"],
    "arm64_smp": ["__cpu_up", "secondary_start_kernel", "cpu_die", "cpu_kill", "handle_IPI", "arch_send_call_function_ipi_mask"],
    "arm64_suspend": ["__cpu_suspend_enter", "cpu_suspend"],
    "arm64_cpuidle": ["arm_cpuidle_suspend"],
    "psci": ["psci_cpu_suspend_enter", "psci_cpu_off", "psci_cpu_on", "psci_system_suspend", "psci_init"],
    "cpuidle_core": ["cpuidle_enter_state", "cpuidle_enter", "cpuidle_idle_call"],
    "gic_v3": ["gic_handle_irq", "gic_cpu_init", "gic_send_sgi"],
    "arch_timer": ["arch_timer_handler_phys", "arch_timer_handler_virt", "arch_timer_handler"],
    "component": ["component_master_add_with_match", "component_bind_all", "try_to_bring_up_master"],
    "driver_core": ["really_probe", "driver_probe_device", "deferred_probe_work_func"],
    "runtime_pm": ["rpm_suspend", "rpm_resume", "__pm_runtime_resume", "__pm_runtime_suspend", "pm_runtime_work"],
    "genpd": ["genpd_power_on", "genpd_power_off", "genpd_runtime_suspend", "genpd_runtime_resume"],
    "workqueue": ["worker_thread", "process_one_work", "flush_workqueue"],
    "sched_core": ["schedule", "__schedule", "scheduler_tick"],
    "qcom_scm": ["qcom_scm_call", "qcom_scm_call_atomic", "qcom_scm_is_available"],
    "qcom_scm_smc": ["__scm_smc_do", "scm_smc_call", "qcom_scm_call_smccc"],
    "rpmh": ["rpmh_write", "rpmh_write_async", "rpmh_rsc_send_data"],
    "rpmh_rsc": ["rpmh_rsc_send_data", "tcs_write", "write_tcs_reg_sync"],
    "msm_drv": ["msm_pdev_probe", "msm_drm_bind", "msm_drm_init", "_msm_drm_init_helper", "msm_open"],
    "msm_atomic": ["msm_atomic_commit", "msm_atomic_commit_tail"],
    "sde_kms": ["sde_kms_init", "sde_kms_hw_init", "sde_kms_prepare_commit", "sde_kms_commit"],
    "dsi_display": ["dsi_display_dev_probe", "dsi_display_bind", "dsi_display_init"],
    "dsi_ctrl": ["dsi_message_tx", "dsi_ctrl_dma_cmd_wait_for_done"],
    "dsi_drm": ["dsi_bridge_pre_enable", "dsi_bridge_enable"],
}

TOKENS = [
    "stop_machine", "stop_cpus", "multi_cpu_stop", "cpu_down", "cpu_up", "cpuhp_",
    "smp_call_function", "local_irq_disable", "local_irq_enable", "raw_local_irq_disable",
    "psci_", "cpu_suspend", "cpu_pm_enter", "cpu_pm_exit", "cpuidle_",
    "qcom_scm", "scm_call", "arm_smccc", "rpmh_", "icc_", "pm_runtime_",
    "pm_genpd", "wait_for_completion", "wait_for_completion_timeout", "flush_workqueue",
    "drm_dev_register", "component_bind_all", "drm_irq_install", "drm_vblank_init",
]

CONFIG_SYMBOLS = [
    "CONFIG_HOTPLUG_CPU", "CONFIG_SMP", "CONFIG_NR_CPUS", "CONFIG_CPU_IDLE",
    "CONFIG_ARM_PSCI_FW", "CONFIG_ARM_PSCI_CPUIDLE", "CONFIG_ARM64", "CONFIG_ARM64_PSEUDO_NMI",
    "CONFIG_PREEMPT", "CONFIG_PREEMPT_NONE", "CONFIG_PREEMPT_VOLUNTARY",
    "CONFIG_NO_HZ", "CONFIG_NO_HZ_IDLE", "CONFIG_NO_HZ_FULL", "CONFIG_HIGH_RES_TIMERS",
    "CONFIG_PM", "CONFIG_PM_SLEEP", "CONFIG_SUSPEND", "CONFIG_CPU_PM", "CONFIG_PM_GENERIC_DOMAINS",
    "CONFIG_QCOM_SCM", "CONFIG_QCOM_RPMH", "CONFIG_QCOM_COMMAND_DB", "CONFIG_INTERCONNECT",
    "CONFIG_INTERCONNECT_QCOM", "CONFIG_QCOM_SPM", "CONFIG_QCOM_LPM", "CONFIG_QCOM_RPMH",
    "CONFIG_ARM_GIC", "CONFIG_ARM_GIC_V3", "CONFIG_GENERIC_IRQ_EFFECTIVE_AFF_MASK",
    "CONFIG_CPU_FREQ", "CONFIG_CPU_FREQ_GOV_SCHEDUTIL", "CONFIG_ARM_QCOM_CPUFREQ_HW",
    "CONFIG_WQ_WATCHDOG", "CONFIG_SOFTLOCKUP_DETECTOR", "CONFIG_HARDLOCKUP_DETECTOR",
    "CONFIG_RCU_STALL_COMMON", "CONFIG_RCU_CPU_STALL_TIMEOUT", "CONFIG_PANIC_ON_OOPS",
]

DT_KEYS = [
    "idle-states", "cpu-idle-states", "psci", "arm,psci", "power-domains", "qcom,spm",
    "qcom,lpm", "rpmh", "qcom,rpmh", "qcom,sleep-status", "qcom,cmd-db", "interconnects",
    "operating-points-v2", "qcom,psci", "domain-idle-states",
]


def discover_touchgrass_defconfig(root: Path) -> Path | None:
    base = root / "arch/arm64/configs"
    if not base.exists():
        return None
    files = [p for p in base.rglob("*") if p.is_file()]
    ranked = sorted(files, key=lambda p: (
        0 if "a52" in p.name.lower() or "a52" in str(p).lower() else 1,
        0 if "defconfig" in p.name.lower() else 1,
        len(str(p)), str(p)))
    return ranked[0] if ranked else None


def dt_snippets(root: Path) -> tuple[list[str], list[Path]]:
    dts_root = root / "arch/arm64/boot/dts"
    if not dts_root.exists():
        return [], []
    all_dts = [p for p in dts_root.rglob("*") if p.suffix in {".dts", ".dtsi"}]
    boardish = [p for p in all_dts if re.search(r"a52|sm7125|sm7150", str(p), re.I)]
    search_files = boardish or all_dts
    snippets: list[str] = []
    for p in search_files:
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if any(k in line for k in DT_KEYS):
                lo = max(0, i - 2); hi = min(len(lines), i + 3)
                snippets.append(f"## {p.relative_to(root)}:{i+1}\n" + "\n".join(f"{n+1}: {lines[n]}" for n in range(lo, hi)))
                if len(snippets) >= 300:
                    return snippets, boardish
    return snippets, boardish


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--gki", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ns = ap.parse_args()
    tg = ns.touchgrass.resolve(); gki = ns.gki.resolve(); project = ns.project.resolve(); out = ns.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "diffs").mkdir(exist_ok=True)

    meta = {
        "touchgrass_root": str(tg), "gki_root": str(gki),
        "touchgrass_sha": sh("git", "rev-parse", "HEAD", cwd=tg),
        "gki_sha": sh("git", "rev-parse", "HEAD", cwd=gki),
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")

    pair_rows = ["label\tcategory\ttouchgrass\tgki\ttg_lines\tgki_lines\tnormalized_equal\tsimilarity"]
    resolved: dict[str, tuple[Path | None, Path | None, str]] = {}
    changed_by_category: dict[str, list[str]] = {}

    for label, cat, tc, gc, hint in FILE_PAIRS:
        tp = resolve(tg, tc, hint)
        gp = resolve(gki, gc, hint)
        resolved[label] = (tp, gp, cat)
        ta = read(tp); ga = read(gp)
        nt = normalize_c(ta); ng = normalize_c(ga)
        ratio = difflib.SequenceMatcher(None, nt[:400000], ng[:400000], autojunk=False).ratio() if ta and ga else 0.0
        equal = bool(ta and ga and nt == ng)
        pair_rows.append("\t".join([
            label, cat, rel(tg, tp), rel(gki, gp), str(len(ta.splitlines())), str(len(ga.splitlines())),
            "yes" if equal else "no", f"{ratio:.4f}",
        ]))
        if not equal:
            changed_by_category.setdefault(cat, []).append(label)
            if ta and ga:
                (out / "diffs" / f"{label}.diff").write_text(limited_diff(ta, ga, f"TG/{rel(tg,tp)}", f"GKI/{rel(gki,gp)}"))
    (out / "file-parity.tsv").write_text("\n".join(pair_rows) + "\n")

    func_rows = ["file\tcategory\tfunction\ttg_present\tgki_present\tnormalized_equal\ttg_sha256\tgki_sha256"]
    high_diffs: list[str] = []
    for label, names in FUNCTIONS.items():
        tp, gp, cat = resolved.get(label, (None, None, "?"))
        ta, ga = read(tp), read(gp)
        for fn in names:
            tf = extract_function(ta, fn) if ta else None
            gf = extract_function(ga, fn) if ga else None
            nt = normalize_c(tf or ""); ng = normalize_c(gf or "")
            eq = bool(tf and gf and nt == ng)
            func_rows.append("\t".join([
                label, cat, fn, "yes" if tf else "no", "yes" if gf else "no", "yes" if eq else "no",
                sha256_text(nt) if tf else "-", sha256_text(ng) if gf else "-",
            ]))
            if tf and gf and not eq:
                d = limited_diff(tf, gf, f"TG:{label}:{fn}", f"GKI:{label}:{fn}", limit=260)
                high_diffs.append(f"\n===== {cat} :: {label} :: {fn} =====\n{d}")
            elif bool(tf) != bool(gf):
                high_diffs.append(f"\n===== {cat} :: {label} :: {fn} =====\nTouchGrass present={bool(tf)} GKI present={bool(gf)}\n")
    (out / "function-parity.tsv").write_text("\n".join(func_rows) + "\n")
    (out / "high-risk-function-diffs.txt").write_text("".join(high_diffs))

    token_rows = ["file\tcategory\ttoken\ttouchgrass_count\tgki_count"]
    for label, (tp, gp, cat) in resolved.items():
        ta, ga = read(tp), read(gp)
        for tok in TOKENS:
            a, b = ta.count(tok), ga.count(tok)
            if a or b:
                token_rows.append(f"{label}\t{cat}\t{tok}\t{a}\t{b}")
    (out / "token-call-matrix.tsv").write_text("\n".join(token_rows) + "\n")

    tgcfg_path = discover_touchgrass_defconfig(tg)
    gkicfg_path = resolve(gki, ["arch/arm64/configs/gki_defconfig", "arch/arm64/configs/defconfig"], "gki_defconfig")
    tgcfg = parse_config(tgcfg_path); gkicfg = parse_config(gkicfg_path)
    cfg_rows = [f"# TouchGrass config: {rel(tg,tgcfg_path)}", f"# GKI config: {rel(gki,gkicfg_path)}", "symbol\ttouchgrass\tgki"]
    for sym in CONFIG_SYMBOLS:
        cfg_rows.append(f"{sym}\t{tgcfg.get(sym,'?')}\t{gkicfg.get(sym,'?')}")
    # Project references show where actual port scripts override/force config; this is not a final .config.
    refs: dict[str, list[str]] = {s: [] for s in CONFIG_SYMBOLS}
    for p in (project / "scripts").glob("*.py"):
        txt = p.read_text(errors="replace")
        for sym in CONFIG_SYMBOLS:
            if sym in txt and len(refs[sym]) < 12:
                refs[sym].append(p.name)
    cfg_rows.append("\n# Project script references (not equivalent to final .config)")
    for sym, files in refs.items():
        if files:
            cfg_rows.append(f"{sym}: {', '.join(files)}")
    (out / "config-power-compare.txt").write_text("\n".join(cfg_rows) + "\n")

    tgdt, tgboards = dt_snippets(tg)
    gkidt, gkiboards = dt_snippets(gki)
    dt_report = [
        "# TouchGrass board-like DTS files", *[rel(tg,p) for p in tgboards[:100]],
        "\n# GKI board-like DTS files", *[rel(gki,p) for p in gkiboards[:100]],
        "\n# TouchGrass power/idle/PSCI snippets", *tgdt,
        "\n# GKI power/idle/PSCI snippets", *gkidt,
    ]
    (out / "dt-power-compare.txt").write_text("\n".join(dt_report) + "\n")

    # Broad source census for key mechanisms. This is intentionally factual: presence/count only.
    census_terms = ["stop_machine", "stop_cpus", "multi_cpu_stop", "cpu_down(", "cpu_up(", "psci_cpu_suspend", "qcom_scm_call", "cpuidle_enter", "rpmh_write", "smp_call_function_many"]
    census: list[str] = []
    for label_root, root in (("TouchGrass", tg), ("GKI", gki)):
        census.append(f"===== {label_root} =====")
        for term in census_terms:
            try:
                cp = subprocess.run(["git", "grep", "-n", "-F", term, "--", "kernel", "arch/arm64", "drivers"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                lines = cp.stdout.splitlines()
            except Exception:
                lines = []
            census.append(f"\n## {term} ({len(lines)} matches)")
            census.extend(lines[:120])
            if len(lines) > 120:
                census.append(f"... {len(lines)-120} more ...")
    (out / "mechanism-census.txt").write_text("\n".join(census) + "\n")

    # Machine-readable summary + conservative report. No automatic claim of causality.
    summary = {
        "metadata": meta,
        "changed_by_category": changed_by_category,
        "touchgrass_defconfig": rel(tg, tgcfg_path),
        "gki_defconfig": rel(gki, gkicfg_path),
        "touchgrass_board_dts_count": len(tgboards),
        "gki_board_dts_count": len(gkiboards),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    md = [
        "# A52 TouchGrass ↔ GKI global-freeze audit",
        "",
        f"TouchGrass: `{meta['touchgrass_sha']}`",
        f"GKI: `{meta['gki_sha']}`",
        "",
        "## Scope",
        "This report compares source paths that can plausibly explain the observed global loss of CPU/IRQ forward progress near 12 s. It distinguishes source differences from causal conclusions; a diff is not by itself proof of the hardware failure.",
        "",
        "## Categories with source differences",
    ]
    for cat in sorted(changed_by_category):
        md.append(f"- **{cat}**: {', '.join(changed_by_category[cat])}")
    md += [
        "",
        "## Evidence-oriented reading order",
        "1. `function-parity.tsv` and `high-risk-function-diffs.txt`: stopper/hotplug/SMP/PSCI/SCM first.",
        "2. `config-power-compare.txt`: CPU idle, PSCI, PM, hotplug, GIC, RPMh/interconnect configuration.",
        "3. `dt-power-compare.txt`: platform low-power/idle-state topology present in TouchGrass source.",
        "4. `file-parity.tsv`: high-level display chain and core subsystem parity.",
        "5. `mechanism-census.txt`: where the suspect mechanisms are invoked in each tree.",
        "",
        "## Important interpretation",
        "The current runtime evidence says the kernel stops globally before userspace opens DRM. Therefore display differences are retained as a possible common low-level cause, but stopper/hotplug/PSCI/SCM/cpuidle/IRQ/power differences should be evaluated before attributing the current blocker to DSI.",
    ]
    (out / "SUMMARY.md").write_text("\n".join(md) + "\n")

    # Integrity manifest.
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    (out / "SHA256SUMS.txt").write_text("\n".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}" for p in files) + "\n")
    print("Phase351 TouchGrass freeze audit: PASS")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
