#!/usr/bin/env python3
"""Phase265: restore Qualcomm IOMMU ownership semantics needed by KGSL.

The Golden Qualcomm 4.19 SMMU maps qcom,iommu-dma="disabled" to an
identity/default-bypass domain. Android 5.10 upstream QCOM SMMU only applies
identity defaults to a fixed compatible list, so downstream context-bank
nodes carrying this property can be given a DMA domain before KGSL attaches
its private domain.

This repair is deliberately narrow:
  * preserve CONFIG_IOMMU_DMA globally;
  * honor qcom,iommu-group indirection like the Qualcomm downstream driver;
  * map only qcom,iommu-dma="disabled" to IOMMU_DOMAIN_IDENTITY;
  * leave every other device on the existing 5.10 policy;
  * emit one sparse F265 marker when the compatibility rule is exercised.

It also writes a non-mutating secure-buffer readiness report for the workflow.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import tempfile

MARKER = "A52_PHASE265_QCOM_IOMMU_DMA_DISABLED_IDENTITY_V1"
SECURE_MARKER = "A52_PHASE265_SECURE_BUFFER_READINESS_V1"

CANDIDATES = (
    "drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c",
    "drivers/iommu/arm-smmu-qcom.c",
)

HELPER = r'''
/* A52_PHASE265_QCOM_IOMMU_DMA_DISABLED_IDENTITY_V1
 *
 * Qualcomm downstream DT semantics: qcom,iommu-dma = "disabled" means the
 * standard DMA-IOMMU layer must not own this client.  Use an identity default
 * domain so a client such as KGSL can subsequently attach its private domain.
 * Keep this policy strictly property-scoped; CONFIG_IOMMU_DMA remains enabled.
 */
static int a52_qcom_iommu_dma_domain_type(struct device *dev)
{
	struct device_node *np;
	const char *mode;
	int type = 0;

	if (!dev->of_node)
		return 0;

	np = of_parse_phandle(dev->of_node, "qcom,iommu-group", 0);
	if (!np)
		np = of_node_get(dev->of_node);
	if (!np)
		return 0;

	if (!of_property_read_string(np, "qcom,iommu-dma", &mode) &&
	    !strcmp(mode, "disabled"))
		type = IOMMU_DOMAIN_IDENTITY;

	of_node_put(np);

	if (type)
		dev_info(dev, "F265 iommu-dma disabled -> identity\n");

	return type;
}

'''


def patch_text(text: str) -> str:
    if MARKER in text:
        return text

    fn_re = re.compile(
        r"static int qcom_smmu_def_domain_type\(struct device \*dev\)\n"
        r"\{(?P<body>.*?)\n\}",
        re.S,
    )
    m = fn_re.search(text)
    if not m:
        raise RuntimeError("qcom_smmu_def_domain_type() not found")

    body = m.group("body")
    if "IOMMU_DOMAIN_IDENTITY" not in body:
        raise RuntimeError("unexpected qcom_smmu_def_domain_type() source shape")

    injected = (
        "\tint a52_type;\n\n"
        "\ta52_type = a52_qcom_iommu_dma_domain_type(dev);\n"
        "\tif (a52_type)\n"
        "\t\treturn a52_type;\n\n"
        + body.lstrip("\n")
    )
    replacement = "static int qcom_smmu_def_domain_type(struct device *dev)\n{\n" + injected + "\n}"
    return text[:m.start()] + HELPER + replacement + text[m.end():]


def find_smmu(root: pathlib.Path) -> pathlib.Path:
    for rel in CANDIDATES:
        p = root / rel
        if p.is_file():
            return p
    raise RuntimeError("no supported QCOM ARM-SMMU implementation found")


def secure_report(root: pathlib.Path) -> pathlib.Path:
    needles = {
        "CONFIG_ION": False,
        "CONFIG_QCOM_SCM": False,
        "qcom_scm_assign_mem": False,
        "hyp_assign_table": False,
        "ion_hyp_assign_sg": False,
        "ion_hyp_unassign_sg": False,
        "secure_heap": False,
    }

    configs = [root / ".config", root.parent.parent / "workspace/gki-phase199-out/.config"]
    for cfg in configs:
        if cfg.is_file():
            s = cfg.read_text(errors="ignore")
            needles["CONFIG_ION"] |= "CONFIG_ION=y" in s
            needles["CONFIG_QCOM_SCM"] |= "CONFIG_QCOM_SCM=y" in s

    scan_roots = [root / "drivers", root / "include"]
    symbols = [k for k in needles if not k.startswith("CONFIG_")]
    for base in scan_roots:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in {".c", ".h", ".Kconfig", ""}:
                continue
            try:
                s = p.read_text(errors="ignore")
            except OSError:
                continue
            for sym in symbols:
                if sym in s:
                    needles[sym] = True

    report = root / "phase265-secure-buffer-readiness.txt"
    lines = [SECURE_MARKER]
    lines += [f"{k}={'present' if v else 'missing'}" for k, v in needles.items()]
    lines.append("NOTE=readiness audit only; missing Golden-only secure symbols are not auto-ported in Phase265")
    report.write_text("\n".join(lines) + "\n")
    return report


def apply(root: pathlib.Path) -> None:
    smmu = find_smmu(root)
    before = smmu.read_text()
    after = patch_text(before)
    smmu.write_text(after)

    final = smmu.read_text()
    required = (
        MARKER,
        'of_parse_phandle(dev->of_node, "qcom,iommu-group", 0)',
        'of_property_read_string(np, "qcom,iommu-dma", &mode)',
        '!strcmp(mode, "disabled")',
        "IOMMU_DOMAIN_IDENTITY",
        "F265 iommu-dma disabled -> identity",
        "a52_qcom_iommu_dma_domain_type(dev)",
    )
    for token in required:
        if token not in final:
            raise RuntimeError(f"Phase265 source gate missing: {token}")

    report = secure_report(root)
    print(f"A52_PHASE265_SMMU={smmu}")
    print(f"A52_PHASE265_SECURE_REPORT={report}")
    print("A52_PHASE265_GFX_IOMMU_PARITY=applied")


def self_test() -> None:
    fixture = '''#include <linux/of_device.h>\n\nstatic int qcom_smmu_def_domain_type(struct device *dev)\n{\n\tconst struct of_device_id *match =\n\t\tof_match_device(qcom_smmu_client_of_match, dev);\n\n\treturn match ? IOMMU_DOMAIN_IDENTITY : 0;\n}\n'''
    patched = patch_text(fixture)
    assert MARKER in patched
    assert patched.count(MARKER) == 1
    assert "a52_qcom_iommu_dma_domain_type(dev)" in patched
    assert '!strcmp(mode, "disabled")' in patched
    assert patch_text(patched) == patched

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        p = root / CANDIDATES[0]
        p.parent.mkdir(parents=True)
        p.write_text(fixture)
        (root / "drivers").mkdir(exist_ok=True)
        (root / "include").mkdir(exist_ok=True)
        apply(root)
        assert (root / "phase265-secure-buffer-readiness.txt").is_file()
    print("A52_PHASE265_SELFTEST=PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="gki/common")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return 0
    apply(pathlib.Path(args.root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
