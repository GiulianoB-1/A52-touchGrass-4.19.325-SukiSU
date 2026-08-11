#!/usr/bin/env python3
"""Phase 254: restore TouchGrass qcom,iommu-dma="disabled" default-domain semantics.

Phase253 hardware progressed past GMU/RPMh but the first explicit KGSL pagetable
still failed in kgsl_device_platform_probe() with -ENOSPC. The live TouchGrass
DT marks gfx3d_user, gfx3d_secure, gmu_user and gmu_kernel context devices with
qcom,iommu-dma="disabled". Downstream ARM-SMMU converts only their auto-created
DMA domains to dynamic software-only domains using logical CB0, so those
default domains do not consume a context bank, rewrite stream routing, or
request a context IRQ. ACK 5.10 lacked that policy and therefore allowed the
default DMA domains to reserve finite context banks before GMU/KGSL created
their explicit unmanaged domains.

This overlay restores only the source-proven "disabled" policy on top of the
Phase253 dynamic-domain implementation and adds K254 diagnostics for the KGSL
SMMU provider. It never fabricates a context bank, forces attach success,
changes a stream ID, or frees somebody else's context-map bit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CFILE = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PHASE253 = "A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1"
MARKER = "A52_PHASE254_KGSL_DISABLED_DEFAULT_DOMAIN_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old[:120]!r}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str, label: str) -> tuple[int, int]:
    pat = re.compile(r"(?m)^[^\n]*\b" + re.escape(name) + r"\s*\(")
    for match in pat.finditer(text):
        start = match.start()
        brace = text.find("{", match.end())
        semi = text.find(";", match.end())
        if brace < 0 or (semi >= 0 and semi < brace):
            continue
        depth = 0
        i = brace
        state = "code"
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ""
            if state == "code":
                if c == '"':
                    state = "str"
                elif c == "'":
                    state = "char"
                elif c == "/" and n == "/":
                    state = "line"; i += 1
                elif c == "/" and n == "*":
                    state = "block"; i += 1
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return start, i + 1
            elif state == "str":
                if c == "\\":
                    i += 1
                elif c == '"':
                    state = "code"
            elif state == "char":
                if c == "\\":
                    i += 1
                elif c == "'":
                    state = "code"
            elif state == "line":
                if c == "\n":
                    state = "code"
            elif state == "block":
                if c == "*" and n == "/":
                    state = "code"; i += 1
            i += 1
    raise RuntimeError(f"{label}: function {name} not found")


def replace_function(text: str, name: str, replacement: str, label: str) -> str:
    start, end = function_bounds(text, name, label)
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


def one_in_function(text: str, name: str, old: str, new: str, label: str) -> str:
    start, end = function_bounds(text, name, label)
    body = text[start:end]
    if new in body:
        return text
    count = body.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one scoped anchor, found {count}: {old[:120]!r}")
    body = body.replace(old, new, 1)
    return text[:start] + body + text[end:]


DT_HELPER = r'''static void a52_arm_smmu_apply_dt_domain_attrs(
		struct iommu_domain *domain,
		struct arm_smmu_domain *smmu_domain, struct device *dev)
{
	struct device_node *np;
	const char *iommu_dma;

	if (!dev->of_node)
		return;

	np = of_parse_phandle(dev->of_node, "qcom,iommu-group", 0);
	if (!np)
		np = of_node_get(dev->of_node);

	/* A52_PHASE254_KGSL_DISABLED_DEFAULT_DOMAIN_V1
	 * TouchGrass calls its default-domain policy only for IOMMU_DOMAIN_DMA.
	 * qcom,iommu-dma="disabled" means a software-only dynamic domain: keep
	 * a valid logical CB index but do not own/program a real CB or stream.
	 */
	if (domain->type == IOMMU_DOMAIN_DMA &&
	    !of_property_read_string(np, "qcom,iommu-dma", &iommu_dma) &&
	    !strcmp(iommu_dma, "disabled")) {
		smmu_domain->attributes |= BIT(DOMAIN_ATTR_DYNAMIC);
		smmu_domain->cfg.cbndx = 0;
		a52_ackfr_record("K254 D disabled dev=%s cb=0", dev_name(dev));
	}

	if (of_property_read_bool(np, "qcom,iommu-earlymap"))
		smmu_domain->attributes |= BIT(DOMAIN_ATTR_EARLY_MAP);

	if (of_property_match_string(np, "qcom,iommu-faults",
				     "non-fatal") >= 0)
		smmu_domain->attributes |= BIT(DOMAIN_ATTR_NON_FATAL_FAULTS);

	if (of_property_read_u32(np, "qcom,iommu-vmid",
				 &smmu_domain->secure_vmid))
		smmu_domain->secure_vmid = VMID_INVAL;

	of_node_put(np);
}'''


CB_OLD = '''\tif (dynamic) {
\t\tif (cfg->cbndx == A52_KGSL_INVALID_CBNDX ||
\t\t    cfg->cbndx >= smmu->num_context_banks) {
\t\t\tret = -EINVAL;
\t\t\tgoto out_unlock;
\t\t}
\t\tret = cfg->cbndx;
\t} else {
\t\tret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);
\t\tif (ret < 0)
\t\t\tgoto out_unlock;
\t}

\tsmmu_domain->smmu = smmu;
\tcfg->cbndx = ret;
'''

CB_NEW = '''\tif (of_device_is_compatible(smmu->dev->of_node, "qcom,smmu-v2"))
\t\ta52_ackfr_record("K254 C pre t=%u d=%d used=%u/%u s2=%u dev=%s",
\t\t\tdomain->type, dynamic,
\t\t\tbitmap_weight(smmu->context_map, smmu->num_context_banks),
\t\t\tsmmu->num_context_banks, smmu->num_s2_context_banks,
\t\t\tdev_name(dev));

\tif (dynamic) {
\t\tif (cfg->cbndx == A52_KGSL_INVALID_CBNDX ||
\t\t    cfg->cbndx >= smmu->num_context_banks)
\t\t\tret = -EINVAL;
\t\telse
\t\t\tret = cfg->cbndx;
\t} else {
\t\tret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);
\t}

\tif (of_device_is_compatible(smmu->dev->of_node, "qcom,smmu-v2"))
\t\ta52_ackfr_record("K254 C alloc t=%u d=%d rc=%d used=%u/%u",
\t\t\tdomain->type, dynamic, ret,
\t\t\tbitmap_weight(smmu->context_map, smmu->num_context_banks),
\t\t\tsmmu->num_context_banks);
\tif (ret < 0)
\t\tgoto out_unlock;

\tsmmu_domain->smmu = smmu;
\tcfg->cbndx = ret;
'''

ASID_OLD = '''\tret = a52_kgsl_init_asid(domain, smmu);
\tif (ret)
\t\tgoto out_clear_smmu;
'''

ASID_NEW = '''\tret = a52_kgsl_init_asid(domain, smmu);
\tif (of_device_is_compatible(smmu->dev->of_node, "qcom,smmu-v2"))
\t\ta52_ackfr_record("K254 A asid t=%u d=%d cb=%u asid=%u rc=%d",
\t\t\tdomain->type, dynamic, cfg->cbndx, cfg->asid, ret);
\tif (ret)
\t\tgoto out_clear_smmu;
'''

SUCCESS_OLD = '''\tif (a52_apps_smmu(smmu->dev) || a52_kgsl_procid_domain(smmu_domain))
\t\ta52_ackfr_record("K253 D init dyn=%d cb=%u asid=%u proc=%u",
\t\t\tdynamic, cfg->cbndx, cfg->asid, cfg->procid);
\treturn 0;
'''

SUCCESS_NEW = '''\tif (of_device_is_compatible(smmu->dev->of_node, "qcom,smmu-v2"))
\t\ta52_ackfr_record("K254 D ok t=%u d=%d cb=%u asid=%u used=%u",
\t\t\tdomain->type, dynamic, cfg->cbndx, cfg->asid,
\t\t\tbitmap_weight(smmu->context_map, smmu->num_context_banks));
\tif (a52_apps_smmu(smmu->dev) || a52_kgsl_procid_domain(smmu_domain))
\t\ta52_ackfr_record("K253 D init dyn=%d cb=%u asid=%u proc=%u",
\t\t\tdynamic, cfg->cbndx, cfg->asid, cfg->procid);
\treturn 0;
'''


def patch_c(text: str, label: str) -> str:
    if MARKER in text:
        validate_c(text, label)
        return text
    if PHASE253 not in text:
        raise RuntimeError(f"{label}: Phase253 domain contract missing")

    text = replace_function(text, "a52_arm_smmu_apply_dt_domain_attrs", DT_HELPER,
                            f"{label}: default-domain DT helper")
    text = one(text,
        "\ta52_arm_smmu_apply_dt_domain_attrs(smmu_domain, dev);\n",
        "\ta52_arm_smmu_apply_dt_domain_attrs(domain, smmu_domain, dev);\n",
        f"{label}: default-domain helper call")
    text = one_in_function(text, "arm_smmu_init_domain_context", CB_OLD, CB_NEW,
                           f"{label}: context-bank diagnostics")
    text = one_in_function(text, "arm_smmu_init_domain_context", ASID_OLD, ASID_NEW,
                           f"{label}: ASID diagnostics")
    text = one_in_function(text, "arm_smmu_init_domain_context", SUCCESS_OLD, SUCCESS_NEW,
                           f"{label}: success diagnostics")
    validate_c(text, label)
    return text


def validate_c(text: str, label: str) -> None:
    required = (
        MARKER,
        PHASE253,
        'domain->type == IOMMU_DOMAIN_DMA',
        '"qcom,iommu-dma"',
        '!strcmp(iommu_dma, "disabled")',
        'smmu_domain->attributes |= BIT(DOMAIN_ATTR_DYNAMIC)',
        'smmu_domain->cfg.cbndx = 0',
        'a52_arm_smmu_apply_dt_domain_attrs(domain, smmu_domain, dev)',
        'K254 D disabled dev=%s cb=0',
        'K254 C pre t=%u d=%d used=%u/%u s2=%u dev=%s',
        'K254 C alloc t=%u d=%d rc=%d used=%u/%u',
        'K254 A asid t=%u d=%d cb=%u asid=%u rc=%d',
        'K254 D ok t=%u d=%d cb=%u asid=%u used=%u',
        'bitmap_weight(smmu->context_map, smmu->num_context_banks)',
        'K253 D init dyn=%d cb=%u asid=%u proc=%u',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token!r}")
    if 'a52_arm_smmu_apply_dt_domain_attrs(smmu_domain, dev)' in text:
        raise RuntimeError(f"{label}: stale Phase208 helper call survived")


def patch_recorder(text: str, label: str) -> str:
    if 'strncmp(fmt, "K254", 4)' in text and '!strncmp(message, "K254 ", 5)' in text:
        return text
    text = one(text,
        'if (strncmp(fmt, "K253", 4) &&\n',
        'if (strncmp(fmt, "K254", 4) &&\n    strncmp(fmt, "K253", 4) &&\n',
        f"{label}: K254 format filter")
    text = one(text,
        'return !strncmp(message, "K253 ", 5) ||\n',
        'return !strncmp(message, "K254 ", 5) ||\n       !strncmp(message, "K253 ", 5) ||\n',
        f"{label}: K254 critical filter")
    return text


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        cp = root / CFILE
        rp = root / RECORDER
        if not (cp.is_file() and rp.is_file()):
            continue
        ct = cp.read_text(encoding="utf-8")
        if PHASE253 not in ct or 'K253 D init dyn=%d cb=%u asid=%u proc=%u' not in ct:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError("expected one generated Phase253 root, found " +
                           (", ".join(map(str, hits)) or "none"))
    return hits[0]


def self_test() -> None:
    old_helper = r'''static void a52_arm_smmu_apply_dt_domain_attrs(
		struct arm_smmu_domain *smmu_domain, struct device *dev)
{
	struct device_node *np;
	if (!dev->of_node)
		return;
	np = of_parse_phandle(dev->of_node, "qcom,iommu-group", 0);
	if (!np)
		np = of_node_get(dev->of_node);
	if (of_property_read_bool(np, "qcom,iommu-earlymap"))
		smmu_domain->attributes |= BIT(DOMAIN_ATTR_EARLY_MAP);
	if (of_property_match_string(np, "qcom,iommu-faults", "non-fatal") >= 0)
		smmu_domain->attributes |= BIT(DOMAIN_ATTR_NON_FATAL_FAULTS);
	if (of_property_read_u32(np, "qcom,iommu-vmid", &smmu_domain->secure_vmid))
		smmu_domain->secure_vmid = VMID_INVAL;
	of_node_put(np);
}'''
    c = (
        "/* " + PHASE253 + " */\n" + old_helper + "\n" +
        '''static int arm_smmu_attach_dev(struct iommu_domain *domain, struct device *dev)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	a52_arm_smmu_apply_dt_domain_attrs(smmu_domain, dev);
	return 0;
}
static int arm_smmu_init_domain_context(struct iommu_domain *domain,
					struct arm_smmu_device *smmu,
					struct device *dev)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct arm_smmu_cfg *cfg = &smmu_domain->cfg;
	bool dynamic = a52_kgsl_dynamic_domain(domain);
	int start = 0, ret = 0;
''' + CB_OLD + ASID_OLD + SUCCESS_OLD + '''
out_clear_smmu:
	return ret;
out_unlock:
	return ret;
}
''')
    c2 = patch_c(c, "fixture/c")
    assert patch_c(c2, "fixture/c2") == c2
    assert 'domain->type == IOMMU_DOMAIN_DMA' in c2
    assert 'K254 C alloc' in c2

    recorder = '''if (strncmp(fmt, "K253", 4) &&
    strncmp(fmt, "K251", 4) &&
    other) return;
return !strncmp(message, "K253 ", 5) ||
       !strncmp(message, "K251 ", 5);
'''
    r2 = patch_recorder(recorder, "fixture/recorder")
    assert 'strncmp(fmt, "K254", 4)' in r2
    assert '!strncmp(message, "K254 ", 5)' in r2
    assert patch_recorder(r2, "fixture/recorder2") == r2
    print("Phase 254 disabled-default-domain overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    cp = root / CFILE
    rp = root / RECORDER
    cp.write_text(patch_c(cp.read_text(encoding="utf-8"), str(cp)), encoding="utf-8")
    rp.write_text(patch_recorder(rp.read_text(encoding="utf-8"), str(rp)), encoding="utf-8")
    print("Phase 254 KGSL disabled default-domain contract applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
