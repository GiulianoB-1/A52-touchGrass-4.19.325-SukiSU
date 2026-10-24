#!/usr/bin/env python3
"""Phase447D allow SE7+SE10 with the proven SE8 GENI GSI/FIFO transport.

No TZ/ADSP SEs.  Existing QUP bus vote, GPI DMA wrapper SID, STM SE8,
phase446AG touch and independently compiled wave gates are preserved.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE447D_SE7_SE8_SE10_I2C_V1"
FILE=Path("drivers/i2c/busses/i2c-qcom-geni.c")
KCONFIG=Path("drivers/a52_447/Kconfig")
PRELUDE=r'''
/* A52_PHASE447D_SE7_SE8_SE10_I2C_V1
 * Hardware offsets verified against A52 R02:
 * SE7  0x984000 SM5714 MFD 0x49, PCA9468 0x57;
 * SE8  0x988000 STM FTS touchscreen; SE10 0x990000 SM5714 USBPD 0x33.
 * Restrict to the exact legacy QUPv3_1 addresses, never scan.
 * a52.i2c_se=8,7,10 can override; default Kconfig mask 0x580.
 */
static u32 a52d_i2c_se_mask=CONFIG_A52_I2C_SE_ALLOW_DEFAULT;
static int __init a52d_i2c_se_param(char *arg)
{
	char *token;
	u32 next=0, n;
	if (!arg || !*arg)
		return -EINVAL;
	while ((token=strsep(&arg,","))) {
		if (kstrtou32(token,0,&n) || (n!=7 && n!=8 && n!=10))
			return -EINVAL;
		next|=BIT(n);
	}
	/* Never enable any QUP engine outside {7,8,10}. */
	a52d_i2c_se_mask=next;
	return 0;
}
early_param("a52.i2c_se",a52d_i2c_se_param);

static int a52d_i2c_se(phys_addr_t addr)
{
	switch (addr) {
	case 0x00984000: return 7;
	case 0x00988000: return 8;
	case 0x00990000: return 10;
	default: return -1;
	}
}
static bool a52d_i2c_legacy_allowed(phys_addr_t addr)
{
	int n=a52d_i2c_se(addr);
	if (n<0 || !(a52d_i2c_se_mask & BIT(n)))
		return false;
	/* Wave0/1: retain SE8-only allowlist; cmdline may override wave. */
	if (n!=8 && !a52_447_enabled(A52_447_SM5714))
		return false;
	return true;
}
'''
def once(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447D {label}: count={n}")
    return s.replace(a,b,1)

def patch(s):
    if TAG in s:return s
    if not all(x in s for x in ("A52_PHASE446AF_GPI_CALLBACK_ABI_FIX_V1",
        "P447B GSI SUMMARY","P446Z I2C BUS_VOTE","P446AE DMA_OWNER")):
        raise RuntimeError("P447D requires complete 447C/446AF/447B GENI source")
    s=once(s,"#include <linux/types.h>",
           "#include <linux/a52_phase447.h>\n#include <linux/types.h>",
           "Phase447 gate header")
    s=once(s,"static int geni_i2c_probe(struct platform_device *pdev)",
           PRELUDE+"\nstatic int geni_i2c_probe(struct platform_device *pdev)",
           "allow-list helpers")
    old='''	/* Restrict all newly supported legacy I2C nodes to SE8 only.
	 * Do not probe TZ/ADSP-owned SEs or unrelated live SE10 at 0x990000.
	 */
	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (legacy && (!res || res->start != 0x00988000)) {
		dev_info(dev,"P446Y I2C SE_SKIP addr=%pa SE8_ONLY=1\\n",
			res ? &res->start : NULL);
		return -ENODEV;
	}'''
    new='''	/* P447D: isolated legacy SE7/8/10 only, gated to Wave2. */
	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (legacy && (!res || !a52d_i2c_legacy_allowed(res->start))) {
		dev_info(dev,"P447D I2C SE_SKIP addr=%pa allow_mask=%#x wave2=%d rc=%d\\n",
			res ? &res->start : NULL,a52d_i2c_se_mask,
			a52_447_enabled(A52_447_SM5714),-ENODEV);
		return -ENODEV;
	}'''
    s=once(s,old,new,"remove SE8_ONLY restriction")
    old='''	gi2c->a52ac_gsi=gi2c->se.a52_legacy_ahb &&
		!!readl_relaxed(gi2c->se.base+0x64);'''
    new='''	gi2c->a52ac_gsi=gi2c->se.a52_legacy_ahb &&
		!!readl_relaxed(gi2c->se.base+0x64);
	if (legacy)
		dev_info(dev,"P447D I2C SE%d mode=%s fifo_disable_ro=%#x base=%pa rc=0\\n",
			a52d_i2c_se(res->start),gi2c->a52ac_gsi?"GSI":"FIFO",
			readl_relaxed(gi2c->se.base+0x64),&res->start);'''
    s=once(s,old,new,"identify per-SE GSI or FIFO at probe")
    old='''	dev_info(dev,"P446Y I2C PROBE_OK bus=%d base=%pa wrapper=%pOF clk=se-clk,m-ahb,s-ahb speed=%u protocol=%u rc=0\\n",
		gi2c->adap.nr,&res->start,dev->of_node,gi2c->clk_freq_out,proto);'''
    new='''	dev_info(dev,"P446Y I2C PROBE_OK bus=%d base=%pa wrapper=%pOF clk=se-clk,m-ahb,s-ahb speed=%u protocol=%u rc=0\\n",
		gi2c->adap.nr,&res->start,dev->of_node,gi2c->clk_freq_out,proto);
	if (legacy)
		dev_info(dev,"P447D I2C SE%d PROBE rc=0 bus=%d mode=%s irq=%d bus_vote=%d dma_owner=%s\\n",
			a52d_i2c_se(res->start),gi2c->adap.nr,
			gi2c->a52ac_gsi?"GSI":"FIFO",gi2c->irq,
			gi2c->a52z_bus_active,
			gi2c->a52ac_gsi && gi2c->a52ae_wrapper_dev ?
				dev_name(gi2c->a52ae_wrapper_dev):"N/A");'''
    s=once(s,old,new,"successful bus probe witness")
    old='''	if (atomic_cmpxchg(&gi2c->a52y_first_xfer,0,1)==0)
		dev_info(gi2c->se.dev,"P446Y I2C FIRST_XFER bus=%d addr=%#x n=%d rc=%d\\n",
			adap->nr,num?msgs[0].addr:0,num,ret);'''
    new='''	if (atomic_cmpxchg(&gi2c->a52y_first_xfer,0,1)==0) {
		dev_info(gi2c->se.dev,"P446Y I2C FIRST_XFER bus=%d addr=%#x n=%d rc=%d\\n",
			adap->nr,num?msgs[0].addr:0,num,ret);
		if (gi2c->se.a52_legacy_ahb)
			dev_info(gi2c->se.dev,"P447D I2C SE%d FIRST_XFER bus=%d addr=%#x mode=%s rc=%d\\n",
				a52d_i2c_se(gi2c->se.base_phys),adap->nr,
				num?msgs[0].addr:0,gi2c->a52ac_gsi?"GSI":"FIFO",ret);
	}'''
    # struct geni_se has no guaranteed base_phys; use original platform resource
    # tracked explicitly on the per-adapter state.
    new=new.replace("a52d_i2c_se(gi2c->se.base_phys)","gi2c->a52d_se")
    s=once(s,old,new,"first transfer result per SE")
    s=once(s,"struct geni_i2c_dev {","struct geni_i2c_dev {\n\tint a52d_se;","per-transport SE id")
    s=once(s,"    gi2c->se.dev = dev;",
           "    gi2c->se.dev = dev;\n"
           "    gi2c->a52d_se=legacy ? a52d_i2c_se(res->start) : -1;",
           "initialize SE identifier before xfer")
    # This is the existing QUPv3_1 wrapper path (SE7+SE8+SE10).
    # Keep its IOMMU/PM votes unchanged; incorrect/missing GSI dmas fail probe.
    return s

def check(root):
    c=(root/FILE).read_text()
    for token in (TAG, "P447D I2C SE%d mode=%s",
                  "P447D I2C SE%d PROBE rc=0",
                  "P447D I2C SE%d FIRST_XFER",
                  'early_param("a52.i2c_se"',
                  "A52_PHASE446AE_GSI_WRAPPER_DMA_V1",
                  "P447B GSI SUMMARY"):
        if token not in c:raise RuntimeError("P447D I2C missing "+token)
    if "SE8_ONLY=1" in c:raise RuntimeError("P447D stale SE8_ONLY restriction")
    assert "config A52_I2C_SE_ALLOW_DEFAULT" in (root/KCONFIG).read_text()
    print("P447D I2C PASS: SE7/8/10 exact allowlist, wave2-gated, dynamic GSI/FIFO, first transfer and DMA-owner witnesses; no TZ/ADSP probes")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/FILE;k=a.root/KCONFIG
    if not a.check_only:
        cfg=k.read_text()
        if "config A52_I2C_SE_ALLOW_DEFAULT" not in cfg:
            cfg+="""
# Phase447D default SE7+SE8+SE10 allow mask; gate W2 still prevents 7/10 on W1.
config A52_I2C_SE_ALLOW_DEFAULT
    hex "A52 safe legacy GENI I2C engine allow mask"
    default 0x580
"""
            k.write_text(cfg)
        f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
