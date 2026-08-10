#!/usr/bin/env python3
"""Phase 249: diagnostic-only GPU SMMU -EBUSY / GMU -ENODEV root corridor.

Phase248 hardware proves the second KGSL probe reaches gmu_iommu_init(), creates
an unmanaged domain for gmu_user, then iommu_attach_device() returns -ENODEV.
The same current boot earlier shows 3d40000.arm,smmu-kgsl entering arm-smmu
probe and returning -EBUSY. This overlay records the exact internal arm-smmu
probe operation that produces that error and whether gmu_user has an IOMMU
group when iommu_attach_device() is called.

No return value, probe order, IOMMU mapping/attach behavior, DT property,
regulator/power vote, fw_devlink state, or recorder transport is changed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
ARM_SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
IOMMU_CORE = Path("drivers/iommu/iommu.c")
GMU = Path("drivers/gpu/msm/kgsl_gmu.c")
CORE = Path("drivers/base/core.c")
CAMCC = Path("drivers/clk/qcom/camcc-lagoon.c")

MARKER = "A52_PHASE249_GPU_SMMU_ENODEV_ROOT_V1"
PHASE248 = "A52_PHASE248_KGSL_GMU_IOMMU_CORRIDOR_V1"
PHASE247 = "A52_PHASE247_CAMCC_DENSE_HWS_V1"
PERMISSIVE = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old!r}")
    return text.replace(old, new, 1)


def find_function(text: str, pattern: str, label: str) -> tuple[int, int]:
    match = re.search(pattern, text, re.M)
    if not match:
        raise RuntimeError(f"{label}: function signature not found")
    brace = text.find("{", match.start(), match.end() + 4)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    depth = 0
    state = "code"
    i = brace
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                state = "block"; i += 2; continue
            if c == "/" and n == "/":
                state = "line"; i += 2; continue
            if c == '"':
                state = "string"; i += 1; continue
            if c == "'":
                state = "char"; i += 1; continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return match.start(), i + 1
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"; i += 2; continue
        elif state == "line":
            if c == "\n":
                state = "code"
        else:
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2; continue
            if c == quote:
                state = "code"
        i += 1
    raise RuntimeError(f"{label}: unterminated function")


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if PHASE248 not in text:
        raise RuntimeError(f"{label}: Phase248 recorder marker missing")
    text = one(
        text,
        'if (strncmp(fmt, "K248", 4) &&\n',
        f'/* {MARKER} */\n'
        'if (strncmp(fmt, "K249", 4) &&\n'
        '    strncmp(fmt, "K248", 4) &&\n',
        f"{label}: format admission",
    )
    text = one(
        text,
        'return !strncmp(message, "K248 ", 5) ||\n',
        'return !strncmp(message, "K249 ", 5) ||\n'
        '       !strncmp(message, "K248 ", 5) ||\n',
        f"{label}: critical admission",
    )
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER, PHASE248, 'strncmp(fmt, "K249", 4)', 'strncmp(fmt, "K248", 4)',
        '!strncmp(message, "K249 ", 5)', '!strncmp(message, "K248 ", 5)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


SMMU_HELPER = f'''\n/* {MARKER} */
static bool a52_k249_gpu_smmu(struct device *dev)
{{
\tconst char *name = dev ? dev_name(dev) : NULL;

\treturn name && strstr(name, "3d40000") && dev->of_node &&
\t\tof_device_is_compatible(dev->of_node, "qcom,smmu-v2");
}}
\n'''


def patch_arm_smmu(text: str, label: str) -> str:
    if "K249 S map in" in text:
        validate_arm_smmu(text, label)
        return text
    if '#include <linux/a52_ack_secure_flight_recorder.h>\n' not in text:
        text = one(text, '#include <linux/slab.h>\n',
                   '#include <linux/slab.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
                   f"{label}: recorder include")
    if '#include <linux/string.h>\n' not in text:
        text = one(text, '#include <linux/slab.h>\n',
                   '#include <linux/slab.h>\n#include <linux/string.h>\n',
                   f"{label}: string include")

    sig = r"static\s+int\s+arm_smmu_device_probe\s*\(\s*struct\s+platform_device\s*\*\s*pdev\s*\)\s*\{"
    start, end = find_function(text, sig, f"{label}: arm_smmu_device_probe")
    fn = text[start:end]
    fn = one(fn, 'irqreturn_t (*global_fault)(int irq, void *dev);\n',
             'irqreturn_t (*global_fault)(int irq, void *dev);\n\tbool k249 = a52_k249_gpu_smmu(dev);\n',
             f"{label}: k249 declaration")
    fn = one(fn, '\tsmmu = devm_kzalloc(dev, sizeof(*smmu), GFP_KERNEL);\n',
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S ent");\n\n'
             '\tsmmu = devm_kzalloc(dev, sizeof(*smmu), GFP_KERNEL);\n',
             f"{label}: probe enter")
    fn = one(fn,
             '\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d",\n',
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S dt rc=%d", err);\n'
             '\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d",\n',
             f"{label}: dt result")
    fn = one(fn,
             '\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n\tif (IS_ERR(smmu->base))\n',
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S map in");\n'
             '\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S map rc=%d",\n'
             '\t\t\tIS_ERR(smmu->base) ? (int) PTR_ERR(smmu->base) : 0);\n'
             '\tif (IS_ERR(smmu->base))\n', f"{label}: map")
    fn = one(fn,
             '\tsmmu = arm_smmu_impl_init(smmu);\n\tif (IS_ERR(smmu)) {\n',
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S impl in");\n'
             '\tsmmu = arm_smmu_impl_init(smmu);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S impl rc=%d",\n'
             '\t\t\tIS_ERR(smmu) ? (int) PTR_ERR(smmu) : 0);\n'
             '\tif (IS_ERR(smmu)) {\n', f"{label}: impl")
    fn = one(fn, '\tif (!smmu->num_context_irqs) {\n',
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S irqs n=%d g=%u c=%u", num_irqs,\n'
             '\t\t\tsmmu->num_global_irqs, smmu->num_context_irqs);\n'
             '\tif (!smmu->num_context_irqs) {\n', f"{label}: irq counts")
    fn = one(fn,
             '\terr = devm_clk_bulk_get_all(dev, &smmu->clks);\n\tif (err < 0) {\n',
             '\terr = devm_clk_bulk_get_all(dev, &smmu->clks);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S clkget rc=%d", err);\n'
             '\tif (err < 0) {\n', f"{label}: clk get")
    fn = one(fn,
             '\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n\tif (err)\n',
             '\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S clkon rc=%d", err);\n'
             '\tif (err)\n', f"{label}: clk enable")
    fn = one(fn,
             '\terr = arm_smmu_device_cfg_probe(smmu);\n\tif (trace)\n',
             '\terr = arm_smmu_device_cfg_probe(smmu);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S cfg rc=%d", err);\n'
             '\tif (trace)\n', f"{label}: cfg")
    fn = one(fn, '\t\terr = devm_request_irq(smmu->dev, smmu->irqs[i],\n',
             '\t\tif (k249)\n\t\t\ta52_ackfr_record("K249 S irq in i=%d n=%d", i, smmu->irqs[i]);\n'
             '\t\terr = devm_request_irq(smmu->dev, smmu->irqs[i],\n', f"{label}: irq enter")
    fn = one(fn,
             '\t\tif (err) {\n\t\t\tdev_err(dev, "failed to request global IRQ %d (%u)\\n",\n',
             '\t\tif (k249)\n\t\t\ta52_ackfr_record("K249 S irq rc=%d i=%d", err, i);\n'
             '\t\tif (err) {\n\t\t\tdev_err(dev, "failed to request global IRQ %d (%u)\\n",\n',
             f"{label}: irq result")
    fn = one(fn,
             '\terr = iommu_device_sysfs_add(&smmu->iommu, smmu->dev, NULL,\n'
             '\t\t\t\t     "smmu.%pa", &ioaddr);\n\tif (err) {\n',
             '\terr = iommu_device_sysfs_add(&smmu->iommu, smmu->dev, NULL,\n'
             '\t\t\t\t     "smmu.%pa", &ioaddr);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S sys rc=%d", err);\n'
             '\tif (err) {\n', f"{label}: sysfs")
    fn = one(fn, '\terr = iommu_device_register(&smmu->iommu);\n\tif (trace)\n',
             '\terr = iommu_device_register(&smmu->iommu);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S reg rc=%d", err);\n'
             '\tif (trace)\n', f"{label}: register")
    fn = one(fn,
             '\tif (!using_legacy_binding) {\n\t\terr = arm_smmu_bus_init(&arm_smmu_ops);\n',
             '\tif (!using_legacy_binding) {\n'
             '\t\tif (k249)\n\t\t\ta52_ackfr_record("K249 S bus in");\n'
             '\t\terr = arm_smmu_bus_init(&arm_smmu_ops);\n'
             '\t\tif (k249)\n\t\t\ta52_ackfr_record("K249 S bus rc=%d", err);\n',
             f"{label}: bus init")
    fn = one(fn,
             '\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe exit rc=0");\n\treturn 0;\n',
             '\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe exit rc=0");\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 S exit rc=0");\n\treturn 0;\n',
             f"{label}: final exit")
    text = text[:start] + SMMU_HELPER + fn + text[end:]
    validate_arm_smmu(text, label)
    return text


def validate_arm_smmu(text: str, label: str) -> None:
    for token in (
        MARKER, "a52_k249_gpu_smmu", "K249 S ent", "K249 S dt rc=%d",
        "K249 S map in", "K249 S map rc=%d", "K249 S impl rc=%d",
        "K249 S irqs n=%d g=%u c=%u", "K249 S clkget rc=%d",
        "K249 S clkon rc=%d", "K249 S cfg rc=%d", "K249 S irq in i=%d n=%d",
        "K249 S irq rc=%d i=%d", "K249 S sys rc=%d", "K249 S reg rc=%d",
        "K249 S bus rc=%d", "K249 S exit rc=0",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


IOMMU_HELPER = f'''\n/* {MARKER} */
static bool a52_k249_gmu_user(struct device *dev)
{{
\treturn dev && dev->of_node && of_node_name_eq(dev->of_node, "gmu_user");
}}
\n'''


def patch_iommu_core(text: str, label: str) -> str:
    if "K249 I grp ok=%d" in text:
        validate_iommu_core(text, label)
        return text
    if '#include <linux/a52_ack_secure_flight_recorder.h>\n' not in text:
        if '#include <linux/iommu.h>\n' not in text:
            raise RuntimeError(f"{label}: iommu include anchor missing")
        text = one(text, '#include <linux/iommu.h>\n',
                   '#include <linux/iommu.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
                   f"{label}: recorder include")
    if '#include <linux/of.h>\n' not in text:
        text = one(text, '#include <linux/iommu.h>\n',
                   '#include <linux/iommu.h>\n#include <linux/of.h>\n', f"{label}: of include")

    sig = r"int\s+iommu_attach_device\s*\(\s*struct\s+iommu_domain\s*\*\s*domain\s*,\s*struct\s+device\s*\*\s*dev\s*\)\s*\{"
    start, end = find_function(text, sig, f"{label}: iommu_attach_device")
    fn = text[start:end]
    fn = one(fn, '\tint ret;\n\n\tgroup = iommu_group_get(dev);\n',
             '\tint ret;\n\tbool k249 = a52_k249_gmu_user(dev);\n\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 I ent");\n'
             '\tgroup = iommu_group_get(dev);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 I grp ok=%d", !!group);\n',
             f"{label}: group state")
    fn = one(fn, '\tif (!group)\n\t\treturn -ENODEV;\n',
             '\tif (!group) {\n'
             '\t\tif (k249)\n\t\t\ta52_ackfr_record("K249 I ret rc=%d s=nogrp", -ENODEV);\n'
             '\t\treturn -ENODEV;\n\t}\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 I g id=%d cnt=%d a=%d",\n'
             '\t\t\tiommu_group_id(group), iommu_group_device_count(group),\n'
             '\t\t\tdomain && domain->ops && domain->ops->attach_dev ? 1 : 0);\n',
             f"{label}: no-group return")
    fn = one(fn, '\tret = __iommu_attach_group(domain, group);\n\nout_unlock:\n',
             '\tret = __iommu_attach_group(domain, group);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 I ag rc=%d", ret);\n\nout_unlock:\n',
             f"{label}: attach group result")
    fn = one(fn, '\tiommu_group_put(group);\n\n\treturn ret;\n',
             '\tiommu_group_put(group);\n'
             '\tif (k249)\n\t\ta52_ackfr_record("K249 I ret rc=%d", ret);\n\n\treturn ret;\n',
             f"{label}: return result")
    text = text[:start] + IOMMU_HELPER + fn + text[end:]
    validate_iommu_core(text, label)
    return text


def validate_iommu_core(text: str, label: str) -> None:
    for token in (
        MARKER, "a52_k249_gmu_user", "K249 I ent", "K249 I grp ok=%d",
        "K249 I ret rc=%d s=nogrp", "K249 I g id=%d cnt=%d a=%d",
        "K249 I ag rc=%d", "K249 I ret rc=%d",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def candidate_roots(args: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        roots.extend((p, p.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate_root(args: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(args):
        paths = [root / p for p in (RECORDER, ARM_SMMU, IOMMU_CORE, GMU, CORE, CAMCC)]
        if not all(p.is_file() for p in paths):
            continue
        recorder = paths[0].read_text(encoding="utf-8")
        gmu = (root / GMU).read_text(encoding="utf-8")
        core = (root / CORE).read_text(encoding="utf-8")
        camcc = (root / CAMCC).read_text(encoding="utf-8")
        if PHASE248 not in recorder or "K248 C att rc=%d n=%.12s" not in gmu:
            continue
        if PERMISSIVE not in core or PHASE247 not in camcc:
            continue
        matches.append(root)
    uniq: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            uniq.append(root)
    if len(uniq) != 1:
        rendered = ", ".join(str(x) for x in uniq) or "none"
        raise RuntimeError(f"expected one generated Phase248 root, found {len(uniq)}: {rendered}")
    return uniq[0]


def apply(root: Path) -> None:
    for rel, fn in ((RECORDER, patch_recorder), (ARM_SMMU, patch_arm_smmu), (IOMMU_CORE, patch_iommu_core)):
        path = root / rel
        original = path.read_text(encoding="utf-8")
        updated = fn(original, str(rel))
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"Phase249 patched {rel}", flush=True)
        else:
            print(f"Phase249 already present {rel}", flush=True)

    recorder = (root / RECORDER).read_text(encoding="utf-8")
    gmu = (root / GMU).read_text(encoding="utf-8")
    core = (root / CORE).read_text(encoding="utf-8")
    camcc = (root / CAMCC).read_text(encoding="utf-8")
    for token in (MARKER, PHASE248, "K248 M iommu rc=%d"):
        if token not in recorder + gmu:
            raise RuntimeError(f"Phase249 retained marker missing: {token}")
    if PERMISSIVE not in core:
        raise RuntimeError("Phase249 lost Phase245 FW_DEVLINK_FLAGS_PERMISSIVE")
    if PHASE247 not in camcc or "cam_cc_pll2_out_early" not in camcc:
        raise RuntimeError("Phase249 lost Phase247 CAMCC dense clk_hws fix")


def self_test() -> None:
    recorder = f'''/* {PHASE248} */\nif (strncmp(fmt, "K248", 4) &&\n    strncmp(fmt, "CXF246", 6))\n\treturn;\nreturn !strncmp(message, "K248 ", 5) ||\n       !strncmp(message, "CXF246 ", 7);\n'''
    patched = patch_recorder(recorder, "fixture-recorder")
    assert MARKER in patched and "K249" in patched

    arm = '''#include <linux/slab.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n\nstatic int arm_smmu_device_probe(struct platform_device *pdev)\n{\n\tstruct resource *res;\n\tresource_size_t ioaddr;\n\tstruct arm_smmu_device *smmu;\n\tstruct device *dev = &pdev->dev;\n\tbool trace = a52_apps_smmu(dev);\n\tint num_irqs, i, err;\n\tirqreturn_t (*global_fault)(int irq, void *dev);\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe enter dev=%s driver=%s", dev_name(dev), "-");\n\n\tsmmu = devm_kzalloc(dev, sizeof(*smmu), GFP_KERNEL);\n\tif (!smmu) return -ENOMEM;\n\tsmmu->dev = dev;\n\tif (dev->of_node)\n\t\terr = arm_smmu_device_dt_probe(pdev, smmu);\n\telse\n\t\terr = arm_smmu_device_acpi_probe(pdev, smmu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d",\n\t\t\terr, err ? -1 : smmu->model, smmu->skip_init, smmu->use_3lvl_tables);\n\tif (err) return err;\n\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n\tif (IS_ERR(smmu->base))\n\t\treturn PTR_ERR(smmu->base);\n\tioaddr = res->start;\n\tsmmu = arm_smmu_impl_init(smmu);\n\tif (IS_ERR(smmu)) {\n\t\terr = PTR_ERR(smmu);\n\t\tif (trace) a52_ackfr_record("SMMU parent-impl rc=%d", err);\n\t\treturn err;\n\t}\n\tif (trace) a52_ackfr_record("SMMU parent-impl rc=0 impl=%d", !!smmu->impl);\n\tnum_irqs = 0;\n\twhile ((res = platform_get_resource(pdev, IORESOURCE_IRQ, num_irqs))) {\n\t\tnum_irqs++;\n\t\tif (num_irqs > smmu->num_global_irqs) smmu->num_context_irqs++;\n\t}\n\tif (!smmu->num_context_irqs) {\n\t\treturn -ENODEV;\n\t}\n\tsmmu->irqs = devm_kcalloc(dev, num_irqs, sizeof(*smmu->irqs), GFP_KERNEL);\n\tfor (i = 0; i < num_irqs; ++i) { smmu->irqs[i] = platform_get_irq(pdev, i); }\n\terr = devm_clk_bulk_get_all(dev, &smmu->clks);\n\tif (err < 0) {\n\t\treturn err;\n\t}\n\tsmmu->num_clks = err;\n\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n\tif (err)\n\t\treturn err;\n\terr = arm_smmu_device_cfg_probe(smmu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-cfg rc=%d groups=%u cbs=%u", err, 0, 0);\n\tif (err)\n\t\treturn err;\n\tif (smmu->impl && smmu->impl->global_fault) global_fault = smmu->impl->global_fault; else global_fault = arm_smmu_global_fault;\n\tfor (i = 0; i < smmu->num_global_irqs; ++i) {\n\t\terr = devm_request_irq(smmu->dev, smmu->irqs[i],\n\t\t\t\t       global_fault, IRQF_SHARED, "arm-smmu global fault", smmu);\n\t\tif (err) {\n\t\t\tdev_err(dev, "failed to request global IRQ %d (%u)\\n",\n\t\t\t\ti, smmu->irqs[i]);\n\t\t\treturn err;\n\t\t}\n\t}\n\terr = iommu_device_sysfs_add(&smmu->iommu, smmu->dev, NULL,\n\t\t\t\t     "smmu.%pa", &ioaddr);\n\tif (err) {\n\t\treturn err;\n\t}\n\tiommu_device_set_ops(&smmu->iommu, &arm_smmu_ops);\n\tiommu_device_set_fwnode(&smmu->iommu, dev->fwnode);\n\terr = iommu_device_register(&smmu->iommu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-register rc=%d", err);\n\tif (err)\n\t\treturn err;\n\tplatform_set_drvdata(pdev, smmu);\n\tif (!using_legacy_binding) {\n\t\terr = arm_smmu_bus_init(&arm_smmu_ops);\n\t\tif (trace) a52_ackfr_record("SMMU parent-probe exit rc=%d", err);\n\t\treturn err;\n\t}\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe exit rc=0");\n\treturn 0;\n}\n'''
    validate_arm_smmu(patch_arm_smmu(arm, "fixture-arm"), "fixture-arm")

    iommu = '''#include <linux/iommu.h>\n\nint iommu_attach_device(struct iommu_domain *domain, struct device *dev)\n{\n\tstruct iommu_group *group;\n\tint ret;\n\n\tgroup = iommu_group_get(dev);\n\tif (!group)\n\t\treturn -ENODEV;\n\n\tmutex_lock(&group->mutex);\n\tret = -EINVAL;\n\tif (iommu_group_device_count(group) != 1)\n\t\tgoto out_unlock;\n\n\tret = __iommu_attach_group(domain, group);\n\nout_unlock:\n\tmutex_unlock(&group->mutex);\n\tiommu_group_put(group);\n\n\treturn ret;\n}\n'''
    validate_iommu_core(patch_iommu_core(iommu, "fixture-iommu"), "fixture-iommu")
    print("Phase 249 overlay self-test: PASS", flush=True)


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        self_test()
        return 0
    root = locate_root(args)
    apply(root)
    print("Phase 249 GPU SMMU / GMU ENODEV diagnostic overlay applied", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 249 overlay failed: {exc}", file=sys.stderr)
        raise
