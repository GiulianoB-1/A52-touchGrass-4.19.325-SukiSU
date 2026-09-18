#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PLATFORM = Path("drivers/base/platform.c")
CORE = Path("drivers/scsi/ufs/ufshcd.c")
QCOM = Path("drivers/scsi/ufs/ufs-qcom.c")
MARK = "A52_PHASE361_UFS_PLATFORM_GATE_V1"


def function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"Phase361 {label}: function anchor not found")
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
    raise SystemExit(f"Phase361 {label}: function end not found")


def line_span(text: str, fn: str, statement: str, label: str) -> tuple[int, int]:
    start, end = function_bounds(text, fn, label)
    wanted = statement.strip()
    matches = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() == wanted:
            matches.append((cursor, line_end))
        cursor = line_end
    if len(matches) != 1:
        raise SystemExit(
            f"Phase361 {label}: expected one '{wanted}', found {len(matches)}"
        )
    return matches[0]


def insert_after(text: str, fn: str, statement: str,
                 insertion: str, label: str) -> str:
    _, end = line_span(text, fn, statement, label)
    return text[:end] + insertion + text[end:]


def add_decl(text: str, anchors: tuple[str, ...], label: str) -> str:
    decl = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if decl in text:
        return text
    for anchor in anchors:
        if anchor in text:
            return text.replace(anchor, anchor + decl, 1)
    raise SystemExit(f"Phase361 {label}: helper declaration anchor missing")


def retained(fmt: str, args: str, indent: str = "\t") -> str:
    # A52GDSC is already in the recorder's post-capacity retention whitelist.
    # Keep Phase361 records under that retained prefix so late UFS retries survive
    # even after the ordinary R48 ring fills.
    return "".join(
        f'{indent}a52_persistent_diag_mark("A52GDSC P361 c={copy} {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def patch_existing_p360(core: str, qcom: str) -> tuple[str, str]:
    # Phase360 used A52_PREPROBE, which is not retained after the main R48 ring
    # fills. Re-prefix every existing Phase360 message with A52GDSC so the exact
    # internal UFS stage survives late boot.
    for copy in (1, 2, 3):
        old = f"A52_PREPROBE copy={copy} P360 "
        new = f"A52GDSC P360 c={copy} "
        core = core.replace(old, new)
        qcom = qcom.replace(old, new)
    return core, qcom


def patch_platform(text: str) -> str:
    if MARK in text:
        return text

    text = add_decl(
        text,
        ("#include <linux/platform_device.h>\n",
         "#include <linux/of_device.h>\n",
         "#include <linux/of.h>\n"),
        "platform helper",
    )

    helper = r'''
static const char a52_p361_marker[] __used = "A52_PHASE361_UFS_PLATFORM_GATE_V1";

static bool a52_p361_ufs_target(struct device *dev)
{
	const char *name = dev ? dev_name(dev) : NULL;

	return name && !strcmp(name, "1d84000.ufshc");
}

'''
    fn = "static int platform_drv_probe(struct device *_dev)"
    start, _ = function_bounds(text, fn, "platform_drv_probe")
    text = text[:start] + helper + text[start:]

    text = insert_after(
        text, fn,
        "ret = of_clk_set_defaults(_dev->of_node, false);",
        "\tif (a52_p361_ufs_target(_dev)) {\n"
        + retained("PLAT clkdef ret=%d drv=%s",
                   'ret, drv ? drv->driver.name : "<none>"', "\t\t")
        + "\t}\n",
        "clock defaults",
    )

    text = insert_after(
        text, fn,
        "ret = dev_pm_domain_attach(_dev, true);",
        "\tif (a52_p361_ufs_target(_dev)) {\n"
        + retained("PLAT pmdom ret=%d drv=%s",
                   'ret, drv ? drv->driver.name : "<none>"', "\t\t")
        + "\t}\n",
        "PM domain attach",
    )

    # Android common 5.10 uses this call in the platform wrapper after the
    # clock-default and PM-domain gates.
    text = insert_after(
        text, fn,
        "ret = drv->probe(dev);",
        "\t\tif (a52_p361_ufs_target(_dev)) {\n"
        + retained("PLAT drvprobe ret=%d drv=%s",
                   'ret, drv ? drv->driver.name : "<none>"', "\t\t\t")
        + "\t\t}\n",
        "driver probe return",
    )

    return text


def validate(platform: str, core: str, qcom: str) -> None:
    required = (
        MARK,
        "P361 c=1 PLAT clkdef",
        "P361 c=1 PLAT pmdom",
        "P361 c=1 PLAT drvprobe",
        "A52GDSC P360 c=1 HBA stage=",
        "A52GDSC P360 c=1 QCOM stage=devm_phy_get",
    )
    joined = platform + core + qcom
    for token in required:
        if token not in joined:
            raise SystemExit("Phase361 required token missing: " + token)
    if "A52_PREPROBE copy=1 P360 " in joined:
        raise SystemExit("Phase361 failed to retain-prefix Phase360 copy1")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    p = ns.root / PLATFORM
    c = ns.root / CORE
    q = ns.root / QCOM
    for path in (p, c, q):
        if not path.is_file():
            raise SystemExit("Phase361 missing source: " + str(path))

    platform = p.read_text(encoding="utf-8")
    core = c.read_text(encoding="utf-8")
    qcom = q.read_text(encoding="utf-8")

    if "A52_PHASE360_UFS_DEFER_PINPOINT_V1" not in core or \
       "A52_PHASE360_UFS_DEFER_PINPOINT_V1" not in qcom:
        raise SystemExit("Phase361 requires Phase360 source lineage")

    if MARK in platform:
        validate(platform, core, qcom)
        print("Phase361 UFS platform gate audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase361 marker missing in check-only mode")

    core, qcom = patch_existing_p360(core, qcom)
    platform = patch_platform(platform)
    validate(platform, core, qcom)

    p.write_text(platform, encoding="utf-8")
    c.write_text(core, encoding="utf-8")
    q.write_text(qcom, encoding="utf-8")
    print("Phase361 UFS platform gate applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
