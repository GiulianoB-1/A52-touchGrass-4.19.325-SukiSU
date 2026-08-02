#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

ATTRS = {
    "early_map": "DOMAIN_ATTR_EARLY_MAP",
    "secure_vmid": "DOMAIN_ATTR_SECURE_VMID",
    "geometry": "DOMAIN_ATTR_GEOMETRY",
    "non_fatal": "DOMAIN_ATTR_NON_FATAL_FAULTS",
    "force_coherent": "DOMAIN_ATTR_PAGE_TABLE_FORCE_COHERENT",
    "upstream_hint": "DOMAIN_ATTR_USE_UPSTREAM_HINT",
    "llc_nwa": "DOMAIN_ATTR_USE_LLC_NWA",
}
PAIR_FILES = (
    ("drivers/a52_display/msm/msm_smmu.c", "techpack/display/msm/msm_smmu.c"),
    ("drivers/a52_display/msm/msm_drv.c", "techpack/display/msm/msm_drv.c"),
    ("drivers/a52_display/msm/sde/sde_kms.c", "techpack/display/msm/sde/sde_kms.c"),
    ("drivers/a52_display/msm/sde/sde_power_handle.c", "techpack/display/msm/sde/sde_power_handle.c"),
    ("drivers/a52_display/msm/sde_rsc.c", "techpack/display/msm/sde_rsc.c"),
    ("drivers/a52_display/msm/dsi/dsi_display.c", "techpack/display/msm/dsi/dsi_display.c"),
    ("drivers/a52_display/msm/dsi/dsi_ctrl.c", "techpack/display/msm/dsi/dsi_ctrl.c"),
    ("drivers/a52_display/msm/dsi/dsi_panel.c", "techpack/display/msm/dsi/dsi_panel.c"),
)


def text(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def align(value: int, page: int) -> int:
    return (value + page - 1) // page * page


def boot_dtb(boot: Path) -> bytes:
    data = boot.read_bytes()
    if data[:8] != b"ANDROID!":
        raise SystemExit("not an Android boot image")
    kernel, _, ramdisk, _, second, _, _, page, header, _ = struct.unpack_from("<10I", data, 8)
    if header != 2:
        raise SystemExit(f"expected boot header v2, got {header}")
    recovery_size = struct.unpack_from("<I", data, 1632)[0]
    recovery_offset = struct.unpack_from("<Q", data, 1636)[0]
    dtb_size = struct.unpack_from("<I", data, 1648)[0]
    second_offset = page + align(kernel, page) + align(ramdisk, page)
    recovery = recovery_offset if recovery_size and recovery_offset else second_offset + align(second, page)
    offset = align(recovery + recovery_size, page) if recovery_size else second_offset
    blob = data[offset:offset + dtb_size]
    if len(blob) != dtb_size or blob[:4] != b"\xd0\r\xfe\xed":
        raise SystemExit("invalid embedded DTB")
    return blob


def cstr(blob: bytes, offset: int) -> str:
    end = blob.find(b"\0", offset)
    return blob[offset:end].decode(errors="replace")


def parse_fdt(blob: bytes) -> dict[str, dict[str, object]]:
    magic, total, off_struct, off_strings, _, _, _, _, strings_size, struct_size = struct.unpack_from(">10I", blob, 0)
    if magic != 0xD00DFEED or total > len(blob):
        raise SystemExit("bad FDT header")
    strings_block = blob[off_strings:off_strings + strings_size]
    block = blob[off_struct:off_struct + struct_size]
    pos = 0
    stack: list[str] = []
    nodes: dict[str, dict[str, object]] = {}
    while pos + 4 <= len(block):
        token = struct.unpack_from(">I", block, pos)[0]
        pos += 4
        if token == 1:
            name = cstr(block, pos)
            pos += len(name.encode()) + 1
            pos = align(pos, 4)
            stack.append(name)
            path = "/" + "/".join(x for x in stack if x)
            nodes[path or "/"] = {}
        elif token == 2:
            if stack:
                stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack_from(">II", block, pos)
            pos += 8
            value = block[pos:pos + length]
            pos = align(pos + length, 4)
            path = "/" + "/".join(x for x in stack if x)
            nodes.setdefault(path or "/", {})[cstr(strings_block, nameoff)] = value
        elif token == 4:
            continue
        elif token == 9:
            break
        else:
            raise SystemExit(f"unknown FDT token {token}")
    return nodes


def strings(value: object) -> list[str]:
    if not isinstance(value, bytes) or not value:
        return []
    parts = value.rstrip(b"\0").split(b"\0")
    if not parts or any(any(byte < 32 or byte > 126 for byte in part) for part in parts):
        return []
    return [part.decode(errors="replace") for part in parts]


def u32s(value: object) -> list[int]:
    if not isinstance(value, bytes) or len(value) % 4:
        return []
    return list(struct.unpack(f">{len(value)//4}I", value))


def compact(props: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    selected = {"compatible", "status", "iommus", "qcom,iommu-dma-addr-pool", "qcom,iommu-faults", "qcom,iommu-vmid", "qcom,iommu-earlymap", "qcom,skip-init", "qcom,use-3-lvl-tables"}
    for key, value in props.items():
        if key not in selected:
            continue
        if value == b"":
            result[key] = True
        elif strings(value):
            result[key] = strings(value)
        else:
            result[key] = u32s(value)
    return result


def find_compat(nodes: dict[str, dict[str, object]], compat: str) -> list[tuple[str, dict[str, object]]]:
    return [(path, props) for path, props in nodes.items() if compat in strings(props.get("compatible"))]


def occurrences(root: Path, needle: str, prefixes: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for prefix in prefixes:
        base = root / prefix
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".c", ".h", ".dts", ".dtsi"}:
                continue
            if needle in text(path):
                found.append(path.relative_to(root).as_posix())
    return sorted(set(found))


def config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        if raw.startswith("CONFIG_") and "=" in raw:
            key, value = raw.split("=", 1)
            result[key[7:]] = value
        elif raw.startswith("# CONFIG_") and raw.endswith(" is not set"):
            result[raw[9:-11]] = "n"
    return result


def finding(severity: str, ident: str, title: str, evidence: list[str], impact: str, action: str, category: str = "compatibility") -> dict[str, object]:
    return {"severity": severity, "id": ident, "title": title, "evidence": evidence, "impact": impact, "recommended_action": action, "category": category}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", required=True, type=Path)
    parser.add_argument("--touchgrass", required=True, type=Path)
    parser.add_argument("--boot", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    gki, touchgrass = args.gki, args.touchgrass
    gki_arm = text(gki / "drivers/iommu/arm/arm-smmu/arm-smmu.c")
    touchgrass_arm = text(touchgrass / "drivers/iommu/arm-smmu.c")
    kms = text(gki / "drivers/a52_display/msm/sde/sde_kms.c")
    drv = text(gki / "drivers/a52_display/msm/msm_drv.c")
    smmu = text(gki / "drivers/a52_display/msm/msm_smmu.c")

    blob = boot_dtb(args.boot)
    nodes = parse_fdt(blob)
    apps = find_compat(nodes, "qcom,qsmmu-v500")
    unsecure = find_compat(nodes, "qcom,smmu_sde_unsec")
    secure = find_compat(nodes, "qcom,smmu_sde_sec")
    sde = find_compat(nodes, "qcom,sde-kms")
    unsecure_props = unsecure[0][1] if unsecure else {}
    secure_props = secure[0][1] if secure else {}

    matrix = {}
    for key, token in ATTRS.items():
        matrix[key] = {
            "token": token,
            "gki_display_calls": token in kms or token in smmu,
            "gki_arm_case": bool(re.search(rf"case\s+{re.escape(token)}\s*:", gki_arm)),
            "touchgrass_arm_case": bool(re.search(rf"case\s+{re.escape(token)}\s*:", touchgrass_arm)),
        }

    scm_tokens = ("scm_io_read", "scm_io_write", "scm_call2", "qcom_scm_io_readl", "qcom_scm_io_writel")
    scm_gki = {name: occurrences(gki, name, ("drivers", "include")) for name in scm_tokens}
    scm_touchgrass = {name: occurrences(touchgrass, name, ("drivers", "include", "techpack")) for name in scm_tokens}

    findings: list[dict[str, object]] = []
    if matrix["early_map"]["gki_display_calls"] and not matrix["early_map"]["gki_arm_case"]:
        findings.append(finding("critical", "missing-early-map-domain-attribute", "KMS calls DOMAIN_ATTR_EARLY_MAP but GKI ARM SMMU does not implement it", [
            f"active unsecure DT has qcom,iommu-earlymap={'qcom,iommu-earlymap' in unsecure_props}",
            "drivers/a52_display/msm/sde/sde_kms.c calls DOMAIN_ATTR_EARLY_MAP",
            "GKI arm_smmu_domain_set_attr has no EARLY_MAP case",
            "TouchGrass implements EARLY_MAP and enables S1 translation when clearing it",
        ], "The first successful display domain reaches set_attribute(), receives -ENODEV and KMS tears down the address spaces.", "Port the narrow TouchGrass early-map state and false-to-enable-S1 semantics. Do not use a silent success stub."))

    if "qcom,iommu-vmid" in secure_props and not matrix["secure_vmid"]["gki_arm_case"]:
        findings.append(finding("high", "missing-secure-vmid-domain-attribute", "Secure display requests VMID but GKI ignores it", [
            f"active secure VMID={u32s(secure_props.get('qcom,iommu-vmid'))}",
            "GKI has no DOMAIN_ATTR_SECURE_VMID case",
            "TouchGrass parses VMID and secures page-table ownership",
        ], "An ordinary domain may be labelled secure without the downstream security contract.", "Keep secure display unavailable until the full secure VMID path is ported and validated.", "future-secure-display"))

    if "qcom,iommu-dma-addr-pool" in unsecure_props and "qcom,iommu-dma-addr-pool" not in gki_arm:
        findings.append(finding("high", "missing-display-iova-pool-contract", "GKI does not parse the downstream display IOVA pool", [
            f"unsecure pool={u32s(unsecure_props.get('qcom,iommu-dma-addr-pool'))}",
            f"secure pool={u32s(secure_props.get('qcom,iommu-dma-addr-pool'))}",
        ], "Continuous-splash and framebuffer mappings can use an aperture different from TouchGrass.", "Port the downstream address-pool geometry contract before relying on later mappings."))

    if "qcom,iommu-faults" in unsecure_props and "qcom,iommu-faults" not in gki_arm:
        findings.append(finding("medium", "missing-display-fault-policy-contract", "GKI does not parse the display non-fatal fault policy", [
            f"unsecure faults={strings(unsecure_props.get('qcom,iommu-faults'))}",
            f"secure faults={strings(secure_props.get('qcom,iommu-faults'))}",
        ], "Display IOMMU faults can be handled differently from the stock kernel.", "Port or explicitly document the non-fatal policy after normal mapping is stable.", "fault-policy"))

    direct_scm = "scm_io_read" in kms or "scm_io_write" in kms
    downstream_direct = bool(scm_gki["scm_call2"])
    upstream_wrapper = bool(scm_gki["qcom_scm_io_readl"] or scm_gki["qcom_scm_io_writel"])
    if direct_scm and upstream_wrapper and not downstream_direct:
        findings.append(finding("critical", "display-direct-scm-still-needs-platform-scm", "Display SCM I/O appears to use unavailable upstream SCM state", scm_gki["qcom_scm_io_readl"][:5] + scm_gki["qcom_scm_io_writel"][:5], "Phase 204 may advance into a later SCM call that still depends on the absent platform SCM device.", "Use the narrow TouchGrass direct-SMC I/O contract or prove the operation is unnecessary. Do not call upstream SCM helpers while availability is false."))
    elif direct_scm and not downstream_direct and not upstream_wrapper:
        findings.append(finding("high", "unclassified-display-scm-compatibility", "Display SCM I/O implementation is not statically classified", scm_gki["scm_io_read"][:5] + scm_gki["scm_io_write"][:5], "The next KMS stage depends on an unverified secure-register ABI.", "Inspect the exact definitions before hardware testing beyond SMMU bring-up."))

    if "qcom,smmu_sde_unsec" in drv and "component_match_add" in drv and "component_add" in smmu:
        findings.append(finding("info", "unsecure-smmu-component-gate-intentional", "The unsecure SMMU component dependency is an intentional 5.10 adaptation", [
            "TouchGrass assumes synchronous child probing",
            "GKI pre-creates the child and waits for component_add",
        ], "This prevents stale-success DRM binding and should be preserved.", "Do not revert this difference while porting SMMU domain contracts.", "intentional-adaptation"))

    rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    findings.sort(key=lambda item: (rank[str(item["severity"])], str(item["id"])))
    blocking = [item["id"] for item in findings if item["severity"] in {"critical", "high"} and item["category"] != "future-secure-display"]

    pairs = []
    for left, right in PAIR_FILES:
        left_path, right_path = gki / left, touchgrass / right
        pairs.append({"gki": left, "touchgrass": right, "gki_present": left_path.is_file(), "touchgrass_present": right_path.is_file(), "gki_sha256": sha(left_path) if left_path.is_file() else None, "touchgrass_sha256": sha(right_path) if right_path.is_file() else None})

    cfg = config(args.config)
    report = {
        "status": "post-smmu-touchgrass-audit-complete",
        "hardware_validated": False,
        "kernel_behavior_changed": False,
        "hardware_test_recommended": not bool(blocking),
        "boot_sha256": sha(args.boot),
        "dtb_sha256": hashlib.sha256(blob).hexdigest(),
        "config_sha256": sha(args.config),
        "file_pairs": pairs,
        "active_dt_nodes": {
            "apps_smmu": [{"path": path, "properties": compact(props)} for path, props in apps],
            "display_unsecure_smmu": [{"path": path, "properties": compact(props)} for path, props in unsecure],
            "display_secure_smmu": [{"path": path, "properties": compact(props)} for path, props in secure],
            "sde": [{"path": path, "properties": compact(props)} for path, props in sde],
        },
        "attribute_matrix": matrix,
        "scm_matrix_gki": scm_gki,
        "scm_matrix_touchgrass": scm_touchgrass,
        "config_focus": {name: cfg.get(name, "<absent>") for name in ("QCOM_SCM", "ARM_SMMU", "IOMMU_SUPPORT", "DISP_CC_LAGOON", "REGULATOR_QPNP_AMOLED", "INTERCONNECT_QCOM_SM6350")},
        "findings": findings,
        "blocking_findings": blocking,
    }
    (args.out / "post-smmu-touchgrass-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    lines = ["# A52 Phase 205 post-SMMU TouchGrass audit", "", "- Kernel behavior changed: **no**", f"- Hardware test recommended: **{'yes' if report['hardware_test_recommended'] else 'no'}**", f"- Boot SHA-256: `{report['boot_sha256']}`", f"- DTB SHA-256: `{report['dtb_sha256']}`", "", "## Findings", ""]
    for item in findings:
        lines += [f"### [{str(item['severity']).upper()}] {item['title']}", "", f"ID: `{item['id']}`", "", "Evidence:"]
        lines += [f"- {entry}" for entry in item["evidence"]]
        lines += ["", f"Impact: {item['impact']}", "", f"Next action: {item['recommended_action']}", ""]
    lines += ["## Decision", "", "Do not add another recorder for a statically proven mismatch. Resolve critical post-SMMU contracts first, then use the existing recorder only at the first boundary that source and DT comparison cannot determine.", ""]
    (args.out / "post-smmu-touchgrass-report.md").write_text("\n".join(lines))
    print(json.dumps({"hardware_test_recommended": report["hardware_test_recommended"], "blocking_findings": blocking, "finding_count": len(findings)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
