#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CORE = Path("drivers/scsi/ufs/ufshcd.c")
QCOM = Path("drivers/scsi/ufs/ufs-qcom.c")
MARK = "A52_PHASE360_UFS_DEFER_PINPOINT_V1"


def function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"Phase360 {label}: function anchor not found: {anchor}")
        brace = text.find("{", start)
        semi = text.find(";", start)
        if brace >= 0 and (semi < 0 or brace < semi):
            break
        search = start + len(anchor)

    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase360 {label}: closing brace not found")


def line_span(text: str, fn: str, statement: str, label: str) -> tuple[int, int]:
    start, end = function_bounds(text, fn, label)
    wanted = statement.strip()
    matches: list[tuple[int, int]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() == wanted:
            matches.append((cursor, line_end))
        cursor = line_end
    if len(matches) != 1:
        raise SystemExit(
            f"Phase360 {label}: expected one '{wanted}' in function, found {len(matches)}"
        )
    return matches[0]


def insert_after_line(text: str, fn: str, statement: str,
                      insertion: str, label: str) -> str:
    _, end = line_span(text, fn, statement, label)
    return text[:end] + insertion + text[end:]


def insert_after_multiline(text: str, fn: str, startswith: str,
                           insertion: str, label: str) -> str:
    start, end = function_bounds(text, fn, label)
    pos = text.find(startswith, start, end)
    if pos < 0:
        raise SystemExit(f"Phase360 {label}: multiline start not found")
    if text.find(startswith, pos + len(startswith), end) >= 0:
        raise SystemExit(f"Phase360 {label}: multiline start is not unique")
    semi = text.find(";", pos, end)
    if semi < 0:
        raise SystemExit(f"Phase360 {label}: statement terminator not found")
    nl = text.find("\n", semi, end)
    point = end if nl < 0 else nl + 1
    return text[:point] + insertion + text[point:]


def add_decl(text: str, anchors: tuple[str, ...], label: str) -> str:
    decl = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if decl in text:
        return text
    for anchor in anchors:
        if anchor in text:
            return text.replace(anchor, anchor + decl, 1)
    raise SystemExit(f"Phase360 {label}: declaration anchor missing")


def triple(fmt: str, args: str, indent: str = "\t") -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("A52_PREPROBE copy={copy} P360 {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def patch_core(text: str) -> str:
    if MARK in text:
        return text

    text = add_decl(
        text,
        ('#include "ufshcd.h"\n', '#include "ufshcd-pltfrm.h"\n'),
        "ufshcd helper",
    )

    marker = f'\nstatic const char a52_p360_core_marker[] __used = "{MARK}";\n\n'
    anchor = "static int ufshcd_hba_init(struct ufs_hba *hba)"
    pos = text.find(anchor)
    if pos < 0:
        raise SystemExit("Phase360: ufshcd_hba_init missing")
    text = text[:pos] + marker + text[pos:]

    fn = "static int ufshcd_hba_init(struct ufs_hba *hba)"
    stages = [
        ("err = ufshcd_init_hba_vreg(hba);", "init_hba_vreg"),
        ("err = ufshcd_setup_hba_vreg(hba, true);", "setup_hba_vreg"),
        ("err = ufshcd_init_clocks(hba);", "init_clocks"),
        ("err = ufshcd_setup_clocks(hba, true);", "setup_clocks"),
        ("err = ufshcd_init_vreg(hba);", "init_device_vreg"),
        ("err = ufshcd_setup_vreg(hba, true);", "setup_device_vreg"),
        ("err = ufshcd_variant_hba_init(hba);", "variant_hba_init"),
    ]
    for statement, name in stages:
        text = insert_after_line(
            text, fn, statement,
            triple(f"HBA stage={name} ret=%d dev=%s",
                   "err, dev_name(hba->dev)"),
            f"HBA stage {name}",
        )

    text = insert_after_line(
        text,
        "static int ufshcd_get_vreg(struct device *dev, struct ufs_vreg *vreg)",
        "vreg->reg = devm_regulator_get(dev, vreg->name);",
        triple(
            "VREG name=%s ret=%ld dev=%s",
            "vreg->name, IS_ERR(vreg->reg) ? PTR_ERR(vreg->reg) : 0L, dev_name(dev)",
        ),
        "regulator get",
    )

    text = insert_after_line(
        text,
        "static int ufshcd_init_clocks(struct ufs_hba *hba)",
        "clki->clk = devm_clk_get(dev, clki->name);",
        triple(
            "CLK name=%s ret=%ld dev=%s",
            "clki->name, IS_ERR(clki->clk) ? PTR_ERR(clki->clk) : 0L, dev_name(dev)",
            "\t\t",
        ),
        "clock get",
    )

    vfn = "static int ufshcd_variant_hba_init(struct ufs_hba *hba)"
    text = insert_after_line(
        text, vfn, "err = ufshcd_vops_init(hba);",
        triple("VOPS stage=init ret=%d variant=%s",
               "err, ufshcd_get_var_name(hba)"),
        "variant init",
    )
    text = insert_after_line(
        text, vfn, "err = ufshcd_vops_setup_regulators(hba, true);",
        triple("VOPS stage=setup_regulators ret=%d variant=%s",
               "err, ufshcd_get_var_name(hba)"),
        "variant regulator setup",
    )
    return text


PHY_HELPER = r'''
static void a52_p360_note_phy_supplier(struct device *dev, struct phy *phy)
{
	struct device_node *np = NULL;
	struct platform_device *pdev = NULL;
	const char *compat = "<none>";
	const char *driver = "<unbound>";
	long ret = IS_ERR(phy) ? PTR_ERR(phy) : 0L;
	int defer = 0;

	if (dev && dev->of_node)
		np = of_parse_phandle(dev->of_node, "phys", 0);
	if (np) {
		of_property_read_string(np, "compatible", &compat);
		pdev = of_find_device_by_node(np);
	}
	if (pdev) {
		if (pdev->dev.driver)
			driver = pdev->dev.driver->name;
		else
			defer = driver_deferred_probe_check_state(&pdev->dev);
	}

	a52_persistent_diag_mark(
		"A52_PREPROBE copy=1 P360 QCOM stage=devm_phy_get ret=%ld supplier=%s compat=%s pdev=%s driver=%s defer=%d\n",
		ret, np ? np->full_name : "<missing>", compat,
		pdev ? dev_name(&pdev->dev) : "<none>", driver, defer);
	a52_persistent_diag_mark(
		"A52_PREPROBE copy=2 P360 QCOM stage=devm_phy_get ret=%ld supplier=%s compat=%s pdev=%s driver=%s defer=%d\n",
		ret, np ? np->full_name : "<missing>", compat,
		pdev ? dev_name(&pdev->dev) : "<none>", driver, defer);
	a52_persistent_diag_mark(
		"A52_PREPROBE copy=3 P360 QCOM stage=devm_phy_get ret=%ld supplier=%s compat=%s pdev=%s driver=%s defer=%d\n",
		ret, np ? np->full_name : "<missing>", compat,
		pdev ? dev_name(&pdev->dev) : "<none>", driver, defer);

	if (pdev)
		put_device(&pdev->dev);
	if (np)
		of_node_put(np);
}

'''


def patch_qcom(text: str) -> str:
    if MARK in text:
        return text

    text = add_decl(
        text,
        ('#include "ufs-qcom.h"\n', '#include "ufshcd.h"\n'),
        "ufs-qcom helper",
    )
    if "#include <linux/of_platform.h>\n" not in text:
        anchor = "#include <linux/platform_device.h>\n"
        if anchor not in text:
            raise SystemExit("Phase360: ufs-qcom platform include missing")
        text = text.replace(anchor, anchor + "#include <linux/of_platform.h>\n", 1)

    fn = "static int ufs_qcom_init(struct ufs_hba *hba)"
    pos, _ = function_bounds(text, fn, "ufs_qcom_init")
    marker = f'\nstatic const char a52_p360_qcom_marker[] __used = "{MARK}";\n'
    text = text[:pos] + marker + PHY_HELPER + text[pos:]

    text = insert_after_line(
        text,
        fn,
        'host->generic_phy = devm_phy_get(dev, "ufsphy");',
        "\ta52_p360_note_phy_supplier(dev, host->generic_phy);\n",
        "QCOM phy get",
    )

    optional = [
        ("err = devm_reset_controller_register(dev, &host->rcdev);",
         "reset_controller_register"),
        ("err = ufs_qcom_bus_register(host);", "bus_register"),
        ("err = ufs_qcom_init_lane_clks(host);", "init_lane_clks"),
        ("err = ufs_qcom_ice_init(host);", "ice_init"),
    ]
    for statement, name in optional:
        try:
            text = insert_after_line(
                text, fn, statement,
                triple(f"QCOM stage={name} ret=%d dev=%s",
                       "err, dev_name(dev)"),
                f"QCOM {name}",
            )
        except SystemExit:
            # Android common tags differ slightly here. PHY resolution is the
            # decisive required marker; these are useful optional refinements.
            pass

    try:
        text = insert_after_multiline(
            text,
            fn,
            'host->device_reset = devm_gpiod_get_optional(dev, "reset",',
            triple(
                "QCOM stage=device_reset_gpio ret=%ld dev=%s",
                "IS_ERR(host->device_reset) ? PTR_ERR(host->device_reset) : 0L, dev_name(dev)",
            ),
            "QCOM device reset GPIO",
        )
    except SystemExit:
        pass

    return text


def validate(core: str, qcom: str) -> None:
    joined = core + qcom
    required = (
        MARK,
        "P360 HBA stage=init_hba_vreg",
        "P360 HBA stage=init_device_vreg",
        "P360 HBA stage=variant_hba_init",
        "P360 VREG name=%s ret=%ld",
        "P360 CLK name=%s ret=%ld",
        "P360 VOPS stage=init",
        "P360 QCOM stage=devm_phy_get",
        "a52_p360_note_phy_supplier(dev, host->generic_phy);",
    )
    for token in required:
        if token not in joined:
            raise SystemExit("Phase360 required token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    core_path = ns.root / CORE
    qcom_path = ns.root / QCOM
    if not core_path.is_file() or not qcom_path.is_file():
        raise SystemExit("Phase360 UFS source missing")

    core_before = core_path.read_text(encoding="utf-8")
    qcom_before = qcom_path.read_text(encoding="utf-8")

    if MARK in core_before and MARK in qcom_before:
        validate(core_before, qcom_before)
        print("Phase360 UFS defer pinpoint audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase360 marker missing in check-only mode")
    if MARK in core_before or MARK in qcom_before:
        raise SystemExit("Phase360 partial application detected")

    core_after = patch_core(core_before)
    qcom_after = patch_qcom(qcom_before)
    validate(core_after, qcom_after)

    core_path.write_text(core_after, encoding="utf-8")
    qcom_path.write_text(qcom_after, encoding="utf-8")
    print("Phase360 UFS defer pinpoint applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
