#!/usr/bin/env python3
"""P453 DAILY: balance A52 legacy QUSB2 power votes and restore cold tuning.

Source is TG's upstream-port PHY, not a new binding. Restrict to the A52
phy-a52-qusb2-v2.c. No magic hard-coded tuning values: first-good init
and probe efuse are the source of truth.
"""
import argparse
from pathlib import Path
PHY=Path("drivers/usb/phy/phy-a52-qusb2-v2.c")
TAG="A52_PHASE453_QUSB2_S2IDLE_RESTORE_V1"
def once(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P453 USB {label} anchors={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:
        return s
    for v in ("A52_PHASE_U1_LEGACY_QUSB2_V2_PORT_V1",
              "static int qusb_phy_enable_power(struct qusb_phy *qphy)",
              "static int qusb_phy_disable_power(struct qusb_phy *qphy)",
              "static int qusb_phy_init(struct usb_phy *phy)",
              "static int qusb_phy_set_suspend(struct usb_phy *phy, int suspend)"):
        if v not in s:raise RuntimeError("P453 USB prerequisite missing "+v)
    s=once(s,'''	bool			override_bias_ctrl2;
};''',
       '''	bool			override_bias_ctrl2;
	/* A52_PHASE453_QUSB2_S2IDLE_RESTORE_V1
	 * Only successful regulator_enable() permits one disable vote.
	 * Duplicate suspend callbacks must not underflow PM6350 LDOs.
	 */
	bool a52_rails_on;
	bool a52_tuning_valid;
	bool a52_efuse_valid;
	u32 a52_efuse_cached;
	u8 a52_imp_ctrl1;
	u8 a52_tune_saved[5];
	u8 a52_bias_saved;
};''',"PHY private state")
    # Guard the physical (not logical) regulator vote. The caller paths
    # are not balanced refcount consumers; one idempotent physical vote
    # prevents both double-enable leaks and double-disable warnings.
    start=s.index("static int qusb_phy_disable_power(struct qusb_phy *qphy)")
    end=s.index("static int qusb_phy_enable_power(struct qusb_phy *qphy)")
    part=s[start:end]
    part=once(part,"int ret = 0;","int ret = 0, first_err = 0;","disable rc")
    part=once(part,"mutex_lock(&qphy->lock);",
       '''mutex_lock(&qphy->lock);
	if (!qphy->a52_rails_on) {
		dev_info(qphy->phy.dev,"P453 QUSB RAIL skip_duplicate_disable\n");
		mutex_unlock(&qphy->lock);
		return 0;
	}''',"disable ownership check")
    for which in ("refgen","vdda33","vdda18","vdd"):
        original=f"ret = regulator_disable(qphy->{which});"
        part=once(part,original,original+"\n\tif (ret && !first_err) first_err = ret;",
                  "retain first disable failure "+which)
    part=once(part,'''	mutex_unlock(&qphy->lock);

	return ret;
}''',
       '''	if (!first_err)
		qphy->a52_rails_on = false;
	else
		dev_err(qphy->phy.dev,
			"P453 QUSB RAIL disable incomplete rc=%d (keep ownership)\n",
			first_err);
	mutex_unlock(&qphy->lock);

	return first_err;
}''',"disable final bookkeeping")
    s=s[:start]+part+s[end:]
    start=s.index("static int qusb_phy_enable_power(struct qusb_phy *qphy)")
    end=s.index("static void qusb_phy_get_tune1_param(",start)
    part=s[start:end]
    part=once(part,"mutex_lock(&qphy->lock);",
      '''mutex_lock(&qphy->lock);
	if (qphy->a52_rails_on) {
		dev_info(qphy->phy.dev,"P453 QUSB RAIL reuse enabled supplies\n");
		mutex_unlock(&qphy->lock);
		return 0;
	}''',"enable ownership check")
    part=once(part,'''	pr_debug("%s(): QUSB PHY's regulators are turned ON.\n", __func__);''',
       '''	qphy->a52_rails_on = true;
	dev_info(qphy->phy.dev,"P453 QUSB RAIL enabled=1 balanced=1\n");
	pr_debug("%s(): QUSB PHY's regulators are turned ON.\n", __func__);''',"mark vote after all rails")
    s=s[:start]+part+s[end:]
    s=once(s,"qphy->tune_val = readl_relaxed(qphy->efuse_reg);",
       '''qphy->tune_val = qphy->a52_efuse_valid ?
		qphy->a52_efuse_cached : readl_relaxed(qphy->efuse_reg);''',
       "do not reread unpowered efuse")
    start=s.index("static int qusb_phy_init(struct usb_phy *phy)")
    end=s.index("static void qusb_phy_shutdown(struct usb_phy *phy)",start)
    part=s[start:end]
    part=once(part,"int p_index;","int p_index, p453_rc;","init rc")
    part=once(part,'''	qusb_phy_reset(qphy);''',
       '''	/* Ensure rails are up BEFORE reset, tune or PLL polling. */
	p453_rc = qusb_phy_enable_power(qphy);
	if (p453_rc) {
		dev_err(phy->dev,"P453 QUSB PHY init rails failed rc=%d\n",p453_rc);
		return p453_rc;
	}
	qusb_phy_reset(qphy);''',"init power before reset")
    part=once(part,'''	/* ensure above writes are completed before re-enabling PHY */
	wmb();''',
       '''	/* Program probe/cold-good values AFTER the DT init sequence.
	 * Suspend can zero the PHY register bank while the efuse is gated.
	 */
	if (qphy->a52_tuning_valid) {
		writel_relaxed(qphy->a52_imp_ctrl1,qphy->base + 0x220);
		for (p_index=0; p_index<5; p_index++)
			writel_relaxed(qphy->a52_tune_saved[p_index],
				qphy->base + 0x240 + 4*p_index);
		writel_relaxed(qphy->a52_bias_saved,qphy->base + 0x198);
		dev_info(phy->dev,
		  "P453 QUSB TUNE restored imp=%u tune1=%u tune2=%u bias=%u\n",
		  qphy->a52_imp_ctrl1,qphy->a52_tune_saved[0],
		  qphy->a52_tune_saved[1],qphy->a52_bias_saved);
	}
	/* ensure above writes are completed before re-enabling PHY */
	wmb();''',"restore cached PHY registers")
    part=once(part,'''	if (!(reg & CORE_READY_STATUS)) {
		dev_err(phy->dev, "QUSB PHY PLL LOCK fails:%x\n", reg);
		WARN_ON(1);
	}
	return 0;''',
       '''	if (!(reg & CORE_READY_STATUS)) {
		dev_err(phy->dev, "P453 QUSB PLL LOCK fails:%x (no WARN storm)\n", reg);
		return -ETIMEDOUT;
	}
	if (!qphy->a52_tuning_valid) {
		qphy->a52_imp_ctrl1=readb_relaxed(qphy->base+0x220);
		for(p_index=0;p_index<5;p_index++)
			qphy->a52_tune_saved[p_index]=
				readb_relaxed(qphy->base+0x240+4*p_index);
		qphy->a52_bias_saved=readb_relaxed(qphy->base+0x198);
		/* Only a PLL-locked cold configuration may seed the cache. */
		qphy->a52_tuning_valid=true;
		dev_info(phy->dev,
		  "P453 QUSB TUNE cached_good imp=%u tune1=%u tune2=%u bias=%u\n",
		  qphy->a52_imp_ctrl1,qphy->a52_tune_saved[0],
		  qphy->a52_tune_saved[1],qphy->a52_bias_saved);
	}
	return 0;''',"cache after successful PLL")
    s=s[:start]+part+s[end:]
    s=once(s,'''	mutex_init(&qphy->lock);
	platform_set_drvdata(pdev, qphy);''',
       '''	/* Read efuse while still in probe. First valid cold init is
	 * authoritative for analog tune/impedance registers.
	 */
	if(qphy->efuse_reg) {
		qphy->a52_efuse_cached=readl_relaxed(qphy->efuse_reg);
		qphy->a52_efuse_valid=true;
		dev_info(dev,"P453 QUSB EFUSE probe_cache=%#x\n",
			qphy->a52_efuse_cached);
	}
	mutex_init(&qphy->lock);
	platform_set_drvdata(pdev, qphy);''',"probe efuse snapshot")
    start=s.index("static int qusb_phy_set_suspend(struct usb_phy *phy, int suspend)")
    end=s.index("static int qusb_phy_notify_connect(",start)
    part=s[start:end]
    part=once(part,"u32 linestate = 0, intr_mask = 0;",
              "u32 linestate = 0, intr_mask = 0;\n\tint p453_rc;","resume rc")
    part=once(part,'''			qusb_phy_enable_power(qphy);
			qusb_phy_enable_clocks(qphy, true);''',
       '''			p453_rc=qusb_phy_enable_power(qphy);
			if(p453_rc)return p453_rc;
			qusb_phy_enable_clocks(qphy, true);''',
       "resume balanced power")
    part=once(part,'''		qphy->suspended = false;''',
       '''		/* Restore the known-good tuning only on a lost PLL,
		 * avoiding a disruptive reset on an active USB session.
		 */
		if(qphy->a52_tuning_valid && !qusb_phy_pll_locked(qphy)) {
			p453_rc=qusb_phy_init(phy);
			if(p453_rc)return p453_rc;
		}
		qphy->suspended = false;''',"resume PLL salvage")
    s=s[:start]+part+s[end:]
    return s
def check(root):
    s=(root/PHY).read_text()
    for key in (TAG,"a52_rails_on","P453 QUSB RAIL skip_duplicate_disable",
                "P453 QUSB RAIL enabled=1","P453 QUSB TUNE restored",
                "P453 QUSB TUNE cached_good","a52_efuse_cached",
                "return -ETIMEDOUT;","p453_rc=qusb_phy_init(phy);"):
        if key not in s:raise RuntimeError("P453 USB source missing "+key)
    if "WARN_ON(1);" in s[s.index("static int qusb_phy_init("):
                         s.index("static void qusb_phy_shutdown(")]:
        raise RuntimeError("P453 PLL WARN_ON still active")
    print("P453 USB PHY PASS: owned PMIC LDO votes, probe efuse, cold-good cached tune, post-resume restore")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args();p=a.root/PHY
    if not a.check_only:p.write_text(patch(p.read_text()))
    check(a.root)
if __name__=="__main__":main()
