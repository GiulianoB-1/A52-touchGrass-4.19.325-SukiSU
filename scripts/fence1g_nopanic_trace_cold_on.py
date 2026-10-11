#!/usr/bin/env python3
"""FENCE1G: keep Phase439 DMA timeout diagnostics but NEVER panic.

FENCE1F proved the first cont=0 recovery reaches P6, the Phase445 gate
opens and the real Samsung ON command enters DMA-wait. Phase439's old
__noreturn panic aborted that first postcold command before its result
or identity could be seen. Replace the fatal timeout handler with a bounded
snapshot/log; preserve the existing DMA wait timeout and complete normally.
Track the ON set message at the Samsung transmit boundary, including
its packet type, payload prefix, length, retry number and transfer rc.
No DSI packets, timing, power, regulators, clocks, PHY, or ESD behavior
are changed.
"""
import argparse
from pathlib import Path

CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
PANEL = Path("drivers/a52_display/msm/dsi/dsi_panel.c")
MARK = "A52_FENCE1G_NO_TIMEOUT_PANIC_V1"
ONMARK = "A52_FENCE1G_COLD_ON_TX_PROBE_V1"

SAFE_HELPER = r'''/* A52_FENCE1G_NO_TIMEOUT_PANIC_V1:
 * The old Phase411/439 diagnostic panic masked legitimate postcold DSI
 * timeout returns; keep the wait_for_completion_timeout's normal flow.
 */
static void a52_p411_timeout_panic(struct dsi_ctrl *dsi_ctrl)
{
    static atomic_t reports = ATOMIC_INIT(0);
    unsigned int num = (unsigned int)atomic_inc_return(&reports);

    if (dsi_ctrl)
        a52_p439_timeout_snapshot(&dsi_ctrl->hw);
    if (num <= 12U) {
        a52_ackfr_record("F1G DMA timeout #%u c=%d irq=%u", num,
            dsi_ctrl ? dsi_ctrl->cell_index : -1,
            dsi_ctrl ?
            (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig) : 0U);
        pr_err("FENCE1G DMA_DONE timeout #%u ctrl=%d irq=%u; NO PANIC, returning via normal DSI timeout path\n",
            num,
            dsi_ctrl ? dsi_ctrl->cell_index : -1,
            dsi_ctrl ?
            (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig) : 0U);
    }
}'''

PRE = r'''		/* A52_FENCE1G_COLD_ON_TX_PROBE_V1: passive per-message
		 * diagnostics, limited to the actual ON set. Capture each retry
		 * before the host transfer can stall in the DMA completion wait.
		 */
#if defined(CONFIG_DISPLAY_SAMSUNG)
		if (type == DSI_CMD_SET_ON && i < 24) {
			const u8 *p = cmds->msg.tx_buf;
			size_t n = cmds->msg.tx_len;
			u8 b0 = p && n > 0 ? p[0] : 0;
			u8 b1 = p && n > 1 ? p[1] : 0;
			u8 b2 = p && n > 2 ? p[2] : 0;
			pr_warn("FENCE1G ON TX idx=%d/%u retry=%d pkt=%02x flags=%x len=%zu b=%02x:%02x:%02x\n",
				i, count, retry, (u32)cmds->msg.type,
				(u32)cmds->msg.flags, n, b0, b1, b2);
			a52_ackfr_record("F1G ON TX i=%d r=%d t=%x b=%02x%02x%02x",
				i, retry, (u32)cmds->msg.type, b0, b1, b2);
		}
#endif
		len = ops->transfer(panel->host, &cmds->msg);
#if defined(CONFIG_DISPLAY_SAMSUNG)
		if (type == DSI_CMD_SET_ON && i < 24) {
			pr_warn("FENCE1G ON RX idx=%d/%u retry=%d len_rc=%zd\n",
				i, count, retry, len);
			a52_ackfr_record("F1G ON RX i=%d rc=%lld",
				i, (long long)len);
		}
#endif
'''

def one(s, old, new, tag):
    if s.count(old) != 1:
        raise RuntimeError("FENCE1G changed anchor %s: count=%d" % (tag, s.count(old)))
    return s.replace(old, new, 1)

def patch_ctrl(s):
    if MARK in s:
        return s
    # End of Phase439 helper must be parsed structurally: later phase patchers
    # add code elsewhere in this 10k-line controller file.
    sig = "static __noreturn void\na52_p411_timeout_panic(struct dsi_ctrl *dsi_ctrl)\n"
    if s.count(sig) != 1:
        raise RuntimeError("FENCE1G Phase439 panic helper signature missing or ambiguous")
    start=s.index(sig)
    brace=s.index("{", start)
    level=0
    end=None
    for i in range(brace,len(s)):
        if s[i] == "{":
            level+=1
        elif s[i] == "}":
            level-=1
            if level==0:
                end=i+1
                break
    if end is None:
        raise RuntimeError("FENCE1G Phase439 helper braces unbalanced")
    old=s[start:end]
    if 'panic("A52P439 DSI F0 DMA_DONE timeout");' not in old:
        raise RuntimeError("FENCE1G expected Phase439 fatal panic not found")
    s=s[:start]+SAFE_HELPER+s[end:]
    # This existing call site is retained. It now returns and falls through
    # the original 'done:' branch, preserving timeout/error propagation.
    if s.count("a52_p411_timeout_panic(dsi_ctrl);") != 1:
        raise RuntimeError("FENCE1G DMA wait timeout call not unique")
    return s

def patch_panel(s):
    if ONMARK in s:
        return s
    target="\t\tlen = ops->transfer(panel->host, &cmds->msg);\n"
    return one(s,target,PRE,"real Samsung command transfer")

def audit(root):
    a=(root/CTRL).read_text()
    b=(root/PANEL).read_text()
    if a.count(MARK)!=1 or b.count(ONMARK)!=1:
        raise RuntimeError("FENCE1G markers absent or duplicated")
    ca=a.index("/* "+MARK)
    cb=a.find("\n}",ca)
    if cb<0 or 'panic("A52P439' in a[ca:cb] or \
            "static __noreturn void\na52_p411_timeout_panic" in a:
        raise RuntimeError("FENCE1G diagnostic timeout is still fatal")
    if a.count("a52_p411_timeout_panic(dsi_ctrl);")!=1:
        raise RuntimeError("FENCE1G missing original timeout call")
    for text in ("len = ops->transfer(panel->host, &cmds->msg);",
        'FENCE1G ON TX idx=', 'FENCE1G ON RX idx=', 'F1G ON TX i=', 'F1G ON RX i='):
        if text not in b:
            raise RuntimeError("FENCE1G panel probe missing: "+text)
    if "A52_PHASE445" not in a:
        raise RuntimeError("FENCE1G Phase445 controller lineage missing")
    print("FENCE1G PASS: Phase439 panic is nonfatal; ON command packet and rc captured; normal DMA timeout preserved")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    args=p.parse_args()
    if not args.check_only:
        ctrl=args.root/CTRL
        panel=args.root/PANEL
        ctrl.write_text(patch_ctrl(ctrl.read_text()))
        panel.write_text(patch_panel(panel.read_text()))
    audit(args.root)

if __name__=="__main__":
    main()
