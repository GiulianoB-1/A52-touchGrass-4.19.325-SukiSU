#!/usr/bin/env python3
"""Phase266: restore Qualcomm KGSL dynamic-domain group semantics on Android 5.10.

Golden Qualcomm/Samsung 4.19 allows KGSL to attach a real/global domain to the
GPU user context bank and subsequently initialise many DOMAIN_ATTR_DYNAMIC
per-process domains against that same context-bank device. The reconstructed
Android 5.10 IOMMU core rejects the second attachment before ARM-SMMU sees it
when group->domain no longer equals group->default_domain.

Phase266 keeps the 5.10 ownership rule for every ordinary IOMMU client and
adds one narrowly-scoped compatibility path:
  * exactly one device in the IOMMU group;
  * device compatible is qcom,smmu-kgsl-cb;
  * requested domain reports DOMAIN_ATTR_DYNAMIC=1.

Such a domain is a shadow attachment: ARM-SMMU finalises its page table/ASID
state, but the IOMMU group's real owner remains KGSL's global domain. Dynamic
detach likewise must not make generic 5.10 re-attach group->default_domain.
"""
from __future__ import annotations

import argparse
import pathlib
import tempfile

MARKER = "A52_PHASE266_KGSL_DYNAMIC_IOMMU_GROUP_COMPAT_V1"

HELPER = r'''
/* A52_PHASE266_KGSL_DYNAMIC_IOMMU_GROUP_COMPAT_V1
 *
 * Qualcomm KGSL dynamic domains share the already-programmed gfx3d user
 * context bank.  They need ARM-SMMU domain finalisation (page tables + ASID),
 * but must not take ownership of the generic IOMMU group.  Keep this exception
 * limited to a single qcom,smmu-kgsl-cb device and DOMAIN_ATTR_DYNAMIC=1.
 */
static atomic_t a52_p266_n = ATOMIC_INIT(0);

static bool a52_p266_kgsl_dynamic_domain(struct iommu_domain *domain,
					 struct iommu_group *group)
{
	struct group_device *grp_dev;
	struct device *dev;
	int dynamic = 0;

	if (!domain || !group || iommu_group_device_count(group) != 1)
		return false;

	if (iommu_domain_get_attr(domain, DOMAIN_ATTR_DYNAMIC, &dynamic) ||
	    !dynamic)
		return false;

	if (list_empty(&group->devices))
		return false;

	grp_dev = list_first_entry(&group->devices, struct group_device, list);
	dev = grp_dev->dev;

	return dev && dev->of_node &&
		of_device_is_compatible(dev->of_node, "qcom,smmu-kgsl-cb");
}

static void a52_p266_trace(const char *tag, struct iommu_group *group, int rc)
{
	unsigned int n = atomic_inc_return(&a52_p266_n);

	if (n <= 96)
		a52_ackfr_record("F266 %s n=%u g=%d rc=%d", tag, n,
				 group ? group->id : -1, rc);
}

'''

OLD_ATTACH = r'''static int __iommu_attach_group(struct iommu_domain *domain,
				struct iommu_group *group)
{
	int ret;

	if (group->default_domain && group->domain != group->default_domain)
		return -EBUSY;

	ret = __iommu_group_for_each_dev(group, domain,
					 iommu_group_do_attach_device);
	if (ret == 0)
		group->domain = domain;

	return ret;
}'''

NEW_ATTACH = r'''static int __iommu_attach_group(struct iommu_domain *domain,
				struct iommu_group *group)
{
	int ret;
	bool a52_dynamic = a52_p266_kgsl_dynamic_domain(domain, group);

	if (group->default_domain && group->domain != group->default_domain &&
	    !a52_dynamic)
		return -EBUSY;

	if (a52_dynamic)
		a52_p266_trace("a0", group, 0);

	ret = __iommu_group_for_each_dev(group, domain,
					 iommu_group_do_attach_device);
	if (ret == 0 && !a52_dynamic)
		group->domain = domain;

	if (a52_dynamic)
		a52_p266_trace("a1", group, ret);

	return ret;
}'''

OLD_DETACH = r'''static void __iommu_detach_group(struct iommu_domain *domain,
				 struct iommu_group *group)
{
	int ret;

	if (!group->default_domain) {
		__iommu_group_for_each_dev(group, domain,
					   iommu_group_do_detach_device);
		group->domain = NULL;
		return;
	}

	if (group->domain == group->default_domain)
		return;

	/* Detach by re-attaching to the default domain */
	ret = __iommu_group_for_each_dev(group, group->default_domain,
					 iommu_group_do_attach_device);
	if (ret != 0)
		WARN_ON(1);
	else
		group->domain = group->default_domain;
}'''

NEW_DETACH = r'''static void __iommu_detach_group(struct iommu_domain *domain,
				 struct iommu_group *group)
{
	int ret;

	if (a52_p266_kgsl_dynamic_domain(domain, group)) {
		a52_p266_trace("d0", group, 0);
		__iommu_group_for_each_dev(group, domain,
					   iommu_group_do_detach_device);
		a52_p266_trace("d1", group, 0);
		return;
	}

	if (!group->default_domain) {
		__iommu_group_for_each_dev(group, domain,
					   iommu_group_do_detach_device);
		group->domain = NULL;
		return;
	}

	if (group->domain == group->default_domain)
		return;

	/* Detach by re-attaching to the default domain */
	ret = __iommu_group_for_each_dev(group, group->default_domain,
					 iommu_group_do_attach_device);
	if (ret != 0)
		WARN_ON(1);
	else
		group->domain = group->default_domain;
}'''


def patch_text(text: str) -> str:
    if MARKER in text:
        return text
    if OLD_ATTACH not in text:
        raise RuntimeError("Phase266: expected Android 5.10 __iommu_attach_group shape not found")
    if OLD_DETACH not in text:
        raise RuntimeError("Phase266: expected Android 5.10 __iommu_detach_group shape not found")

    if "#include <linux/of.h>" not in text:
        anchor = "#include <linux/iommu.h>\n"
        if anchor not in text:
            raise RuntimeError("Phase266: linux/iommu.h include anchor not found")
        text = text.replace(anchor, anchor + "#include <linux/of.h>\n", 1)
    if "#include <linux/atomic.h>" not in text:
        anchor = "#include <linux/kernel.h>\n"
        if anchor not in text:
            raise RuntimeError("Phase266: linux/kernel.h include anchor not found")
        text = text.replace(anchor, anchor + "#include <linux/atomic.h>\n", 1)

    text = text.replace(OLD_ATTACH, HELPER + OLD_ATTACH, 1)
    text = text.replace(OLD_ATTACH, NEW_ATTACH, 1)
    text = text.replace(OLD_DETACH, NEW_DETACH, 1)
    return text


def verify_lower_contract(root: pathlib.Path) -> None:
    smmu_candidates = (
        root / "drivers/iommu/arm/arm-smmu/arm-smmu.c",
        root / "drivers/iommu/arm-smmu.c",
    )
    smmu = next((p for p in smmu_candidates if p.is_file()), None)
    if smmu is None:
        raise RuntimeError("Phase266: ARM-SMMU source not found")
    s = smmu.read_text(errors="ignore")
    for token in (
        "A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1",
        "DOMAIN_ATTR_DYNAMIC",
        "a52_kgsl_dynamic_domain(domain)",
        "if (a52_kgsl_dynamic_domain(domain))",
    ):
        if token not in s:
            raise RuntimeError(f"Phase266: lower ARM-SMMU dynamic contract missing: {token}")

    kgsl = root / "drivers/gpu/msm/kgsl_iommu.c"
    if not kgsl.is_file():
        raise RuntimeError("Phase266: reconstructed KGSL IOMMU source not found")
    k = kgsl.read_text(errors="ignore")
    for token in (
        "DOMAIN_ATTR_DYNAMIC",
        "DOMAIN_ATTR_CONTEXT_BANK",
        "DOMAIN_ATTR_TTBR0",
        "F261 it",
    ):
        if token not in k:
            raise RuntimeError(f"Phase266: KGSL dynamic page-table contract missing: {token}")


def apply(root: pathlib.Path) -> None:
    verify_lower_contract(root)
    core = root / "drivers/iommu/iommu.c"
    if not core.is_file():
        raise RuntimeError("Phase266: drivers/iommu/iommu.c not found")
    core.write_text(patch_text(core.read_text()))

    final = core.read_text()
    for token in (
        MARKER,
        "a52_p266_kgsl_dynamic_domain",
        'of_device_is_compatible(dev->of_node, "qcom,smmu-kgsl-cb")',
        "iommu_domain_get_attr(domain, DOMAIN_ATTR_DYNAMIC, &dynamic)",
        'a52_p266_trace("a0", group, 0)',
        'a52_p266_trace("a1", group, ret)',
        'a52_p266_trace("d0", group, 0)',
        'a52_ackfr_record("F266 %s n=%u g=%d rc=%d"',
    ):
        if token not in final:
            raise RuntimeError(f"Phase266 source gate missing: {token}")
    if "group->domain != group->default_domain &&\n\t    !a52_dynamic" not in final:
        raise RuntimeError("Phase266: generic ownership guard was not preserved")

    print(f"A52_PHASE266_IOMMU_CORE={core}")
    print("A52_PHASE266_KGSL_DYNAMIC_IOMMU_GROUP_COMPAT=applied")


def self_test() -> None:
    fixture = '''#include <linux/device.h>\n#include <linux/kernel.h>\n#include <linux/iommu.h>\n#include <linux/of.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n\nstruct group_device { struct list_head list; struct device *dev; };\nstruct iommu_group { struct list_head devices; struct iommu_domain *default_domain; struct iommu_domain *domain; int id; };\n\nstatic int iommu_group_do_attach_device(struct device *dev, void *data) { return 0; }\nstatic int iommu_group_do_detach_device(struct device *dev, void *data) { return 0; }\nstatic int __iommu_group_for_each_dev(struct iommu_group *group, void *data, int (*fn)(struct device *, void *)) { return 0; }\nstatic int iommu_group_device_count(struct iommu_group *group) { return 1; }\n\n''' + OLD_ATTACH + '\n\n' + OLD_DETACH + '\n'
    patched = patch_text(fixture)
    assert MARKER in patched
    assert patched.count(MARKER) == 1
    assert "&&\n\t    !a52_dynamic" in patched
    assert "ret == 0 && !a52_dynamic" in patched
    assert 'a52_p266_trace("d0", group, 0)' in patched
    assert patch_text(patched) == patched

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "drivers/iommu").mkdir(parents=True)
        (root / "drivers/iommu/iommu.c").write_text(fixture)
        (root / "drivers/iommu/arm/arm-smmu").mkdir(parents=True)
        (root / "drivers/iommu/arm/arm-smmu/arm-smmu.c").write_text(
            "A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1\n"
            "DOMAIN_ATTR_DYNAMIC\n"
            "a52_kgsl_dynamic_domain(domain)\n"
            "if (a52_kgsl_dynamic_domain(domain)) {}\n"
        )
        (root / "drivers/gpu/msm").mkdir(parents=True)
        (root / "drivers/gpu/msm/kgsl_iommu.c").write_text(
            "DOMAIN_ATTR_DYNAMIC DOMAIN_ATTR_CONTEXT_BANK DOMAIN_ATTR_TTBR0 F261 it\n"
        )
        apply(root)
    print("A52_PHASE266_SELFTEST=PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="gki/common")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
    else:
        apply(pathlib.Path(args.root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
