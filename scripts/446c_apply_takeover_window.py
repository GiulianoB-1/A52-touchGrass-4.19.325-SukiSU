#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_PHASE446C_TAKEOVER_WINDOW_V1"


def die(msg: str) -> None:
    raise SystemExit("Phase446c: " + msg)


def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return s.replace(old, new, 1)


def insert_after_line_once(s: str, needle: str, addition: str, label: str) -> str:
    n = s.count(needle)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    p = s.index(needle) + len(needle)
    return s[:p] + addition + s[p:]


def add_boot_switches(s: str) -> str:
    if MARK in s:
        return s

    if "#include <linux/init.h>" not in s:
        m = list(re.finditer(r"(?m)^#include[^\n]*\n", s))
        if not m:
            die("no include block")
        p = m[-1].end()
        s = s[:p] + "#include <linux/init.h>\n" + s[p:]

    anchor = "static int sde_kms_hw_init(struct msm_kms *kms);\n"
    block = r'''
/* A52_PHASE446C_TAKEOVER_WINDOW_V1
 * Diagnostic-only boot switches. Both default false.
 * keep_earlymap preserves the firmware early-map attribute only for the
 * unsecure display domain. skip_post_enable_init skips only the explicit
 * initial POST_ENABLE call in sde_kms_hw_init; registered later callbacks
 * remain untouched.
 */
static const char a52_p446c_marker[] __used = "A52_PHASE446C_TAKEOVER_WINDOW_V1";
static bool a52_p446c_keep_earlymap;
static bool a52_p446c_skip_post_enable_init;

static int __init a52_p446c_keep_earlymap_setup(char *str)
{
	if (str && (*str == '1' || *str == 'y' || *str == 'Y'))
		a52_p446c_keep_earlymap = true;
	return 1;
}
__setup("a52.keep_earlymap=", a52_p446c_keep_earlymap_setup);

static int __init a52_p446c_skip_post_enable_setup(char *str)
{
	if (str && (*str == '1' || *str == 'y' || *str == 'Y'))
		a52_p446c_skip_post_enable_init = true;
	return 1;
}
__setup("a52.skip_post_enable_init=", a52_p446c_skip_post_enable_setup);
'''
    s = one(s, anchor, anchor + block, "boot switch declarations")
    return s


def patch_earlymap(s: str) -> str:
    if "A52_PHASE446C_KEEP_EARLYMAP_GATE" in s:
        return s

    pat = re.compile(
        r"(?P<ind>\t+)ret = mmu->funcs->set_attribute\(mmu, DOMAIN_ATTR_EARLY_MAP,\n"
        r"(?P=ind)\t\t &early_map\);\n"
        r"(?P=ind)a52_p446_mark\(0x126U,\(u32\)i,\(u32\)ret\);\n"
    )
    m = pat.search(s)
    if not m:
        # Accept the pre-446b source too, to keep the patch reusable.
        pat = re.compile(
            r"(?P<ind>\t+)ret = mmu->funcs->set_attribute\(mmu, DOMAIN_ATTR_EARLY_MAP,\n"
            r"(?P=ind)\t\t &early_map\);\n"
        )
        m = pat.search(s)
    if not m:
        die("EARLY_MAP set_attribute anchor missing")

    ind = m.group("ind")
    repl = (
        ind + "/* A52_PHASE446C_KEEP_EARLYMAP_GATE */\n"
        + ind + "if (a52_p446c_keep_earlymap && i == MSM_SMMU_DOMAIN_UNSECURE) {\n"
        + ind + "\tret = 0;\n"
        + ind + "\ta52_p446_mark(0x150U, (u32)i, 1U);\n"
        + ind + "} else {\n"
        + ind + "\tret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,\n"
        + ind + "\t\t\t &early_map);\n"
        + ind + "}\n"
        + ind + "a52_p446_mark(0x126U,(u32)i,(u32)ret);\n"
    )
    return s[:m.start()] + repl + s[m.end():]


def patch_shared_hw(s: str) -> str:
    if "A52_PHASE446C_SHARED_HW_SUBSTEPS" in s:
        return s
    old = '''static void sde_kms_init_shared_hw(struct sde_kms *sde_kms)
{
	if (!sde_kms || !sde_kms->hw_mdp || !sde_kms->catalog)
		return;

	if (sde_kms->hw_mdp->ops.reset_ubwc)
		sde_kms->hw_mdp->ops.reset_ubwc(sde_kms->hw_mdp,
						sde_kms->catalog);

	sde_hw_sid_rotator_set(sde_kms->hw_sid);
}
'''
    new = '''static void sde_kms_init_shared_hw(struct sde_kms *sde_kms)
{
	/* A52_PHASE446C_SHARED_HW_SUBSTEPS */
	if (!sde_kms || !sde_kms->hw_mdp || !sde_kms->catalog)
		return;

	a52_p446_mark(0x146U, 0U, 0U);
	if (sde_kms->hw_mdp->ops.reset_ubwc)
		sde_kms->hw_mdp->ops.reset_ubwc(sde_kms->hw_mdp,
						sde_kms->catalog);
	a52_p446_mark(0x147U, 0U, 0U);

	a52_p446_mark(0x148U, 0U, 0U);
	sde_hw_sid_rotator_set(sde_kms->hw_sid);
	a52_p446_mark(0x149U, 0U, 0U);
}
'''
    return one(s, old, new, "shared HW substeps")


def patch_post_enable(s: str) -> str:
    if "A52_PHASE446C_POST_ENABLE_SUBSTEPS" in s:
        return s

    old = '''	if (event_type == SDE_POWER_EVENT_POST_ENABLE) {
		sde_irq_update(msm_kms, true);
		sde_vbif_init_memtypes(sde_kms);
		sde_kms_init_shared_hw(sde_kms);
		_sde_kms_set_lutdma_vbif_remap(sde_kms);
		sde_kms->first_kickoff = true;
		sde_kms_update_pm_qos_irq_request(sde_kms, true, true);
	} else if (event_type == SDE_POWER_EVENT_PRE_DISABLE) {
'''
    new = '''	if (event_type == SDE_POWER_EVENT_POST_ENABLE) {
		/* A52_PHASE446C_POST_ENABLE_SUBSTEPS */
		a52_p446_mark(0x141U, 0U, 0U);
		a52_p446_mark(0x142U, 0U, 0U);
		sde_irq_update(msm_kms, true);
		a52_p446_mark(0x143U, 0U, 0U);
		a52_p446_mark(0x144U, 0U, 0U);
		sde_vbif_init_memtypes(sde_kms);
		a52_p446_mark(0x145U, 0U, 0U);
		sde_kms_init_shared_hw(sde_kms);
		a52_p446_mark(0x14aU, 0U, 0U);
		_sde_kms_set_lutdma_vbif_remap(sde_kms);
		a52_p446_mark(0x14bU, 0U, 0U);
		sde_kms->first_kickoff = true;
		a52_p446_mark(0x14cU, 1U, 0U);
		a52_p446_mark(0x14dU, 0U, 0U);
		sde_kms_update_pm_qos_irq_request(sde_kms, true, true);
		a52_p446_mark(0x14eU, 0U, 0U);
		a52_p446_mark(0x14fU, 0U, 0U);
	} else if (event_type == SDE_POWER_EVENT_PRE_DISABLE) {
'''
    return one(s, old, new, "POST_ENABLE substeps")


def patch_hw_blocks(s: str) -> str:
    if "A52_PHASE446C_KMS_BLOCK_SUBSTEPS" in s:
        return s

    anchor = '''static int _sde_kms_hw_init_blocks(struct sde_kms *sde_kms,
	struct drm_device *dev,
	struct msm_drm_private *priv)
{
'''
    s = one(s, anchor, anchor + '\t/* A52_PHASE446C_KMS_BLOCK_SUBSTEPS */\n', "block-entry")
    decl = "\tint i, rc = -EINVAL;\n"
    p = s.find(anchor)
    q = s.find(decl, p)
    if q < 0:
        die("block declaration anchor missing")
    q += len(decl)
    s = s[:q] + "\n\ta52_p446_mark(0x130U, 0U, 0U);\n" + s[q:]

    pairs = [
        ("\trc = _sde_kms_mmu_init(sde_kms);\n",
         "\ta52_p446_mark(0x131U, 0U, 0U);\n",
         "\ta52_p446_mark(0x132U, (u32)rc, 0U);\n",
         "mmu-init"),
        ("\trc = sde_reg_dma_init(sde_kms->reg_dma, sde_kms->catalog,\n\t\t\tsde_kms->dev);\n",
         "\ta52_p446_mark(0x133U, 0U, 0U);\n",
         "\ta52_p446_mark(0x134U, (u32)rc, 0U);\n",
         "reg-dma"),
        ("\trc = sde_rm_init(rm, sde_kms->catalog, sde_kms->mmio,\n\t\t\tsde_kms->dev);\n",
         "\ta52_p446_mark(0x135U, 0U, 0U);\n",
         "\ta52_p446_mark(0x136U, (u32)rc, 0U);\n",
         "rm-init"),
    ]
    for call, pre, post, label in pairs:
        if call not in s:
            die(label + " anchor missing")
        s = s.replace(call, pre + call + post, 1)

    old = '''	sde_kms->hw_intr = sde_hw_intr_init(sde_kms->mmio, sde_kms->catalog);
'''
    s = one(s, old,
            "\ta52_p446_mark(0x137U, 0U, 0U);\n" + old +
            "\ta52_p446_mark(0x138U, IS_ERR_OR_NULL(sde_kms->hw_intr) ? 1U : 0U, 0U);\n",
            "hw-intr")

    old = '''	sde_dbg_init_dbg_buses(sde_kms->core_rev);
'''
    s = one(s, old,
            "\ta52_p446_mark(0x152U, 0U, 0U);\n" + old +
            "\ta52_p446_mark(0x153U, 0U, 0U);\n",
            "debug-bus-init")

    old = '''	sde_kms->hw_mdp = sde_rm_get_mdp(&sde_kms->rm);
'''
    s = one(s, old,
            "\ta52_p446_mark(0x154U, 0U, 0U);\n" + old +
            "\ta52_p446_mark(0x155U, IS_ERR_OR_NULL(sde_kms->hw_mdp) ? 1U : 0U, 0U);\n",
            "get-mdp")

    old = '''		sde_kms->hw_vbif[i] = sde_hw_vbif_init(vbif_idx,
				sde_kms->vbif[vbif_idx], sde_kms->catalog);
'''
    new = '''		a52_p446_mark(0x139U, (u32)i, vbif_idx);
		sde_kms->hw_vbif[i] = sde_hw_vbif_init(vbif_idx,
				sde_kms->vbif[vbif_idx], sde_kms->catalog);
		a52_p446_mark(0x13aU, (u32)i,
			IS_ERR_OR_NULL(sde_kms->hw_vbif[i]) ? 1U : 0U);
'''
    s = one(s, old, new, "vbif-init")

    old = '''	sde_kms->hw_sid = sde_hw_sid_init(sde_kms->sid,
				sde_kms->sid_len, sde_kms->catalog);
'''
    s = one(s, old,
            "\ta52_p446_mark(0x13bU, 0U, 0U);\n" + old +
            "\ta52_p446_mark(0x13cU, IS_ERR(sde_kms->hw_sid) ? 1U : 0U, 0U);\n",
            "sid-init")

    old = '''	rc = _sde_kms_drm_obj_init(sde_kms);
'''
    s = one(s, old,
            "\ta52_p446_mark(0x13dU, 0U, 0U);\n" + old +
            "\ta52_p446_mark(0x13eU, (u32)rc, 0U);\n",
            "drm-obj-init")
    return s


def patch_hw_init(s: str) -> str:
    if "A52_PHASE446C_INITIAL_POST_ENABLE_GATE" in s:
        return s

    old = '''	sde_kms_handle_power_event(SDE_POWER_EVENT_POST_ENABLE, sde_kms);
	sde_kms->power_event = sde_power_handle_register_event(&priv->phandle,
'''
    new = '''	/* A52_PHASE446C_INITIAL_POST_ENABLE_GATE */
	a52_p446_mark(0x140U, (u32)rc, 0U);
	if (a52_p446c_skip_post_enable_init) {
		a52_p446_mark(0x151U, 1U, 0U);
	} else {
		sde_kms_handle_power_event(SDE_POWER_EVENT_POST_ENABLE, sde_kms);
	}
	sde_kms->power_event = sde_power_handle_register_event(&priv->phandle,
'''
    return one(s, old, new, "initial POST_ENABLE gate")


def patch(path: Path) -> None:
    s = path.read_text(errors="replace")
    s = add_boot_switches(s)
    s = patch_earlymap(s)
    s = patch_shared_hw(s)
    s = patch_post_enable(s)
    s = patch_hw_blocks(s)
    s = patch_hw_init(s)
    path.write_text(s)


def check(path: Path) -> None:
    s = path.read_text(errors="replace")
    required = (
        MARK,
        'a52.keep_earlymap=',
        'a52.skip_post_enable_init=',
        'A52_PHASE446C_KEEP_EARLYMAP_GATE',
        'A52_PHASE446C_SHARED_HW_SUBSTEPS',
        'A52_PHASE446C_POST_ENABLE_SUBSTEPS',
        'A52_PHASE446C_KMS_BLOCK_SUBSTEPS',
        'A52_PHASE446C_INITIAL_POST_ENABLE_GATE',
        'a52_p446_mark(0x150U',
        'a52_p446_mark(0x151U',
        'a52_p446_mark(0x139U',
        'a52_p446_mark(0x13aU',
        'a52_p446_mark(0x142U',
        'a52_p446_mark(0x143U',
        'a52_p446_mark(0x144U',
        'a52_p446_mark(0x145U',
        'a52_p446_mark(0x146U',
        'a52_p446_mark(0x147U',
        'a52_p446_mark(0x148U',
        'a52_p446_mark(0x149U',
        'a52_p446_mark(0x14aU',
        'a52_p446_mark(0x14bU',
        'a52_p446_mark(0x14fU',
        'a52_p446_mark(0x152U',
        'a52_p446_mark(0x153U',
        'a52_p446_mark(0x154U',
        'a52_p446_mark(0x155U',
    )
    missing = [x for x in required if x not in s]
    if missing:
        die("contract missing: " + ", ".join(missing))

    if s.count('sde_kms_handle_power_event(SDE_POWER_EVENT_POST_ENABLE, sde_kms);') != 1:
        die("expected exactly one gated initial POST_ENABLE call")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--kind", choices=("gki",), default="gki")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    path = a.root / "drivers/a52_display/msm/sde/sde_kms.c"
    if not path.is_file():
        die(f"missing {path}")

    if not a.check_only:
        patch(path)
    check(path)
    print("Phase446c GKI: takeover-window substeps + one-shot boot switches PASS")


if __name__ == "__main__":
    main()
