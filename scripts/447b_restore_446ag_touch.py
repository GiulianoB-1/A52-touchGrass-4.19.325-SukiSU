#!/usr/bin/env python3
"""447B: restore must-keep 446AG touch behavior on assembled 446AF source.

Read-only STM firmware bringup; immediate probe, prevent IRQ-depth operations
before request_threaded_irq, and rate-gate informational GSI transport logging.
Anchors are checked on compiled-source predecessors, not raw TG 4.19.
"""
import argparse,re
from pathlib import Path
TAG="A52_PHASE447B_TOUCH_AG_V1"
TS=Path("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c")
I2C=Path("drivers/i2c/busses/i2c-qcom-geni.c")
def once(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447B {label}: expected 1 anchor, found {n}")
    return s.replace(a,b,1)
def touch(s):
    if TAG in s:return s
    if "A52_PHASE446AF_TOUCH_PROBE_FAILFAST_V1" not in s:
        raise RuntimeError("P447B requires Phase446AF STM")
    s=once(s,"static unsigned int a52ad_touch_delay_ms = 8000;",
        "/* "+TAG+" */\nstatic unsigned int a52ad_touch_delay_ms = 0;",
        "default STM probe delay")
    s=once(s,"\trc = fts_fw_update_on_probe(info);",
        '\tpr_info_once("P447B TOUCH FW_REQUEST_SKIPPED fw_write_locked=1\\n");\n'
        "\trc = 0;",
        "skip probe firmware request entirely")
    start=s.index("void fts_interrupt_set(struct fts_ts_info *info, int enable)")
    end=s.index("void fts_ic_interrupt_set(",start)
    frag=s[start:end]
    frag=once(frag,
        "\tstruct irq_desc *desc = irq_to_desc(info->irq);",
        r"""	struct irq_desc *desc;
	/* Avoid enable_irq/disable_irq before request_threaded_irq succeeds. */
	if (!READ_ONCE(info->irq_enabled)) {
		pr_info_once("P447B TOUCH IRQ_DEPTH_SKIP before request_irq\n");
		return;
	}
	desc = irq_to_desc(info->irq);
	if (unlikely(!desc)) {
		pr_err("P447B TOUCH IRQ_DESC_NULL irq=%d\n",info->irq);
		return;
	}""",
        "IRQ registration precedes IRQ depth operations")
    s=s[:start]+frag+s[end:]
    return s
def gsi(s):
    if TAG in s:return s
    s=once(s,"static void a52ac_gsi_tx_cb(void *arg)",
        "static atomic_t a52ag_xfer_count=ATOMIC_INIT(0);\n"
        "static void a52ac_gsi_tx_cb(void *arg)",
        "global GSI log counter")
    for label in ("TX","RX"):
        s=once(s,
            '\tdev_info_ratelimited(gi2c->se.dev,\n\t\t"P446AF GSI '+label+'_CB',
            '\tif (atomic_read(&a52ag_xfer_count)<=20)\n'
            '\t\tdev_info_ratelimited(gi2c->se.dev,\n\t\t"P446AF GSI '+label+'_CB',
            "guard GSI "+label+" completion log")
    start=s.index("static int a52ac_gsi_xfer(")
    end=s.index("static int geni_i2c_xfer(",start)
    frag=s[start:end]
    frag=once(frag,
        "\tint i,ret=0;",
        """	int i,ret=0;
	static unsigned long a52ag_last_summary;
	bool a52ag_verbose=(atomic_inc_return(&a52ag_xfer_count)<=20);
""".rstrip(),
        "first 20 GSI requests")
    # Suppress INFO spam after first 20; errors remain unconditional.
    frag,n=re.subn(r"(?m)^([ \t]*)(dev_info(?:_ratelimited)?\()",r"\1if (a52ag_verbose) \2",frag)
    if n<3:raise RuntimeError("P447B expected >=3 informational GSI lines, found "+str(n))
    frag=once(frag,
        "\treturn ret;\n}",
        r"""	if (time_after(jiffies,READ_ONCE(a52ag_last_summary)+5*HZ)) {
		WRITE_ONCE(a52ag_last_summary,jiffies);
		dev_info(dev,"P447B GSI SUMMARY xfers=%d rc=%d first20_only=1\n",
			atomic_read(&a52ag_xfer_count),ret);
	}
	return ret;
}""",
        "five-second GSI summary")
    return s[:start]+frag+s[end:]
def check(root):
    t=(root/TS).read_text()
    i=(root/I2C).read_text()
    for marker in (TAG,"a52ad_touch_delay_ms = 0;",
                   "P447B TOUCH FW_REQUEST_SKIPPED","P447B TOUCH IRQ_DEPTH_SKIP"):
        if marker not in t:raise RuntimeError("P447B touch missing "+marker)
    if "\trc = fts_fw_update_on_probe(info);" in t:
        raise RuntimeError("P447B legacy STM firmware request still invoked")
    for marker in ("first20_only=1","P447B GSI SUMMARY","a52ag_verbose",
                   "if (atomic_read(&a52ag_xfer_count)<=20)"):
        if marker not in i:raise RuntimeError("P447B GSI missing "+marker)
    print("P447B TOUCH AG PASS: no firmware request, default delay=0, guarded IRQ depth, first20+5s summary")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:
        t=a.root/TS;i=a.root/I2C
        tt=touch(t.read_text());ii=gsi(i.read_text())
        t.write_text(tt);i.write_text(ii)
    check(a.root)
if __name__=="__main__":main()
