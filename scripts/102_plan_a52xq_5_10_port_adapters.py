#!/usr/bin/env python3
"""Turn Workflow 101 compile diagnostics into an ordered port-adapter plan."""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


RULES: list[dict[str, Any]] = [
    {
        "id": "P1-header-bridges",
        "phase": 1,
        "risk": "low",
        "title": "Restore removed or relocated compatibility headers",
        "patterns": [r"file not found"],
        "next_action": (
            "Add narrowly scoped compatibility headers or copy the exact downstream "
            "declarations where the 5.10 tree has no equivalent. Do not expose a broad "
            "4.19 include tree ahead of native 5.10 headers."
        ),
    },
    {
        "id": "P1-duplicate-compat-definitions",
        "phase": 1,
        "risk": "low",
        "title": "Guard compatibility declarations already provided by 5.10",
        "patterns": [r"redefinition of", r"attribute declaration must precede definition"],
        "next_action": (
            "Prefer the native 5.10 declaration and make the downstream compatibility "
            "definition conditional or remove it."
        ),
    },
    {
        "id": "P2-mm-time-vm",
        "phase": 2,
        "risk": "medium",
        "title": "Adapt memory accounting, mmap locking and timekeeping APIs",
        "patterns": [
            r"MM_UNRECLAIMABLE", r"NR_UNRECLAIMABLE_PAGES", r"mmap_sem",
            r"struct timespec", r"getboottime", r"getnstimeofday", r"get_ds",
            r"vm_fault_t", r"vm_insert_pfn", r"__mutex_owner",
        ],
        "next_action": (
            "Create local wrapper helpers that map the removed 4.19 interfaces to the "
            "5.10 mmap lock, timespec64 and vm fault interfaces. Validate semantics per call site."
        ),
    },
    {
        "id": "P2-interrupt-tasklet",
        "phase": 2,
        "risk": "medium",
        "title": "Restore interrupt and tasklet type visibility",
        "patterns": [r"irq_handler_t", r"irqreturn_t", r"IRQ_HANDLED", r"tasklet"],
        "next_action": (
            "Add the correct native 5.10 interrupt and tasklet headers first. Only add an "
            "adapter when the downstream callback signature differs."
        ),
    },
    {
        "id": "P2-clock-regulator",
        "phase": 2,
        "risk": "medium",
        "title": "Adapt downstream clock and regulator helper APIs",
        "patterns": [r"clk_set_flags", r"clk_regmap_div", r"rpmh_mode_solver_set", r"PTR_RET"],
        "next_action": (
            "Replace removed helper calls with local wrappers and translate downstream "
            "clock structure initializers to the 5.10 field layout."
        ),
    },
    {
        "id": "P2-pm-qos",
        "phase": 2,
        "risk": "medium",
        "title": "Translate legacy PM QoS requests",
        "patterns": [r"pm_qos_", r"cpus_affine"],
        "next_action": (
            "Map legacy PM QoS requests to the 5.10 frequency or latency QoS interfaces "
            "at each call site."
        ),
    },
    {
        "id": "P3-iommu-dma-cache",
        "phase": 3,
        "risk": "high",
        "title": "Port Qualcomm IOMMU, DMA and cache-maintenance behavior",
        "patterns": [
            r"IOMMU_FAULT_", r"DOMAIN_ATTR_", r"DMA_ATTR_", r"arch_setup_dma_ops",
            r"dmac_", r"dma-iommu", r"kmap_atomic_flush_unused",
        ],
        "next_action": (
            "Do not use numeric aliases blindly. Translate each downstream fault or domain "
            "attribute to the 5.10 IOMMU model and replace ARM cache calls with supported DMA APIs."
        ),
    },
    {
        "id": "P3-display-formats-notifiers",
        "phase": 3,
        "risk": "high",
        "title": "Restore downstream display formats, FPS and notifier contracts",
        "patterns": [r"V4L2_PIX_FMT_SDE_", r"FPS[0-9]+", r"drm_panel_notifier", r"VFL_TYPE_GRABBER"],
        "next_action": (
            "Import only the A52 formats and notifier ABI required by the preserved device "
            "tree and userspace contract, then isolate them behind A52-specific headers."
        ),
    },
    {
        "id": "P3-qseecom-secure-buffer",
        "phase": 3,
        "risk": "high",
        "title": "Repair QSEECom and secure-buffer interfaces",
        "patterns": [r"qseecom", r"secure_buffer", r"trace_secure_buffer", r"smcinvoke"],
        "next_action": (
            "Fix declaration ownership and internal include paths first, then validate ioctl "
            "and shared-memory semantics against the Android 16 userspace contract."
        ),
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def classify(message: str, file_path: str) -> dict[str, Any]:
    haystack = f"{file_path}\n{message}"
    for rule in RULES:
        if any(re.search(pattern, haystack, re.IGNORECASE) for pattern in rule["patterns"]):
            return rule
    return {
        "id": "P4-manual-review",
        "phase": 4,
        "risk": "unknown",
        "title": "Manual call-site review",
        "next_action": "Inspect the exact source context and choose a semantic 5.10 replacement.",
    }


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    artifact = args.artifact.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    final_state_path = artifact / "final-state.json"
    incompat_path = artifact / "compile" / "compile-incompatibilities.json"
    ownership_path = artifact / "driver-ownership-matrix.tsv"
    for required in (final_state_path, incompat_path, ownership_path):
        if not required.is_file():
            raise SystemExit(f"missing Workflow 101 input: {required}")

    final_state = json.loads(final_state_path.read_text())
    incompat = json.loads(incompat_path.read_text())
    if final_state.get("status") != "downstream-port-compile-probe-complete":
        raise SystemExit("unexpected Workflow 101 status")
    if final_state.get("flashable") is not False:
        raise SystemExit("Workflow 101 input must remain non-flashable")

    signatures: list[dict[str, Any]] = incompat.get("unique_signatures", [])
    entries: list[dict[str, Any]] = incompat.get("entries", [])
    expected_errors = int(incompat.get("compiler_error_count", -1))
    if expected_errors != len(entries):
        raise SystemExit(f"error count mismatch: summary={expected_errors}, entries={len(entries)}")

    signature_rows: list[dict[str, Any]] = []
    family_counts: collections.Counter[str] = collections.Counter()
    phase_counts: collections.Counter[int] = collections.Counter()
    risk_counts: collections.Counter[str] = collections.Counter()

    for signature in signatures:
        rule = classify(signature["message"], signature.get("subsystem", ""))
        count = int(signature["count"])
        family_counts[rule["id"]] += count
        phase_counts[int(rule["phase"])] += count
        risk_counts[str(rule["risk"])] += count
        signature_rows.append({
            "phase": rule["phase"],
            "risk": rule["risk"],
            "adapter_family": rule["id"],
            "subsystem": signature.get("subsystem", ""),
            "count": count,
            "message": signature["message"],
        })

    file_counts = collections.Counter(entry["file"] for entry in entries)
    file_family_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for entry in entries:
        rule = classify(entry["message"], entry["file"])
        file_family_counts[entry["file"]][rule["id"]] += 1

    hotspot_rows = []
    for file_path, count in file_counts.most_common():
        families = file_family_counts[file_path]
        hotspot_rows.append({
            "count": count,
            "file": file_path,
            "dominant_adapter_family": families.most_common(1)[0][0],
            "family_breakdown": ",".join(f"{name}:{value}" for name, value in families.most_common()),
        })

    signature_rows.sort(key=lambda row: (int(row["phase"]), -int(row["count"]), row["message"]))
    write_tsv(output / "adapter-signatures.tsv",
              ["phase", "risk", "adapter_family", "subsystem", "count", "message"],
              signature_rows)
    write_tsv(output / "file-hotspots.tsv",
              ["count", "file", "dominant_adapter_family", "family_breakdown"],
              hotspot_rows)

    rule_by_id = {rule["id"]: rule for rule in RULES}
    manifest_families = []
    for family_id, count in family_counts.most_common():
        rule = rule_by_id.get(family_id) or {
            "id": family_id,
            "phase": 4,
            "risk": "unknown",
            "title": "Manual call-site review",
            "next_action": "Inspect exact source context.",
        }
        manifest_families.append({
            "id": family_id,
            "phase": rule["phase"],
            "risk": rule["risk"],
            "title": rule["title"],
            "error_count": count,
            "next_action": rule["next_action"],
        })

    manifest = {
        "status": "workflow101-port-adapter-plan-complete",
        "source_workflow": 101,
        "source_flashable": False,
        "source_hardware_validated": False,
        "compiler_error_count": expected_errors,
        "unique_error_signature_count": len(signatures),
        "adapter_family_count": len(manifest_families),
        "phase_error_counts": {str(key): value for key, value in sorted(phase_counts.items())},
        "risk_error_counts": dict(risk_counts),
        "families": sorted(manifest_families, key=lambda item: (item["phase"], -item["error_count"])),
        "recommended_next_workflow": {
            "number": 103,
            "scope": "phase-1-low-risk-compatibility-shims-and-recompile",
            "flashable": False,
            "hardware_validated": False,
        },
    }
    (output / "adapter-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    md = [
        "# A52 4.19 to 5.10 port-adapter plan",
        "",
        f"Workflow 101 captured **{expected_errors}** compiler errors and **{len(signatures)}** unique signatures.",
        "This Workflow 102 artifact is analysis-only and is not flashable.",
        "",
        "## Ordered adapter phases",
        "",
    ]
    for family in manifest["families"]:
        md.extend([
            f"### Phase {family['phase']}: {family['title']}",
            "",
            f"- Adapter family: `{family['id']}`",
            f"- Classified errors: **{family['error_count']}**",
            f"- Risk: **{family['risk']}**",
            f"- Next action: {family['next_action']}",
            "",
        ])
    md.extend([
        "## Recommended next build",
        "",
        "Workflow 103 should implement only Phase 1 low-risk header bridges and duplicate-definition guards,",
        "then rerun the same full keep-going compile probe. It must remain non-flashable until the complete",
        "display, KGSL and secure-service port links successfully and the boot image preservation audit passes.",
        "",
    ])
    (output / "PORT-ADAPTER-PLAN.md").write_text("\n".join(md))
    (output / "README-FIRST.txt").write_text(
        "A52 5.10 PORT ADAPTER PLAN\n\n"
        "This Workflow 102 artifact classifies every Workflow 101 compiler error into an ordered migration plan.\n"
        "It contains no kernel Image and is not flashable.\n\n"
        "Read PORT-ADAPTER-PLAN.md first, then adapter-signatures.tsv and file-hotspots.tsv.\n"
    )

    files = [
        output / "README-FIRST.txt",
        output / "PORT-ADAPTER-PLAN.md",
        output / "adapter-manifest.json",
        output / "adapter-signatures.tsv",
        output / "file-hotspots.tsv",
    ]
    (output / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))

    print(json.dumps({
        "compiler_errors": expected_errors,
        "unique_signatures": len(signatures),
        "adapter_families": len(manifest_families),
        "phase_counts": dict(sorted(phase_counts.items())),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
