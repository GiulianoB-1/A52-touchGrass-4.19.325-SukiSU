#!/usr/bin/env python3
"""446AF: fix confirmed TG GPI completion-callback ABI memory corruption.

The original 446AC path passed gi2c as descriptor->callback_param.  Pinned TG
gpi.c always interprets it as struct msm_gpi_dma_async_tx_cb_param and writes
length/code/status/imed_tre into the first 28 bytes, corrupting gi2c (which
contains the DMA channel pointers).  Keep a persistent typed callback parameter
per channel and convey gi2c only in its userdata field, as pinned TG does.
"""
import argparse
from pathlib import Path

I2C=Path("drivers/i2c/busses/i2c-qcom-geni.c")
TOUCH=Path("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c")
TAG="A52_PHASE446AF_GPI_CALLBACK_ABI_FIX_V1"

def once(s,a,b,name):
    n=s.count(a)
    if n!=1:
        raise RuntimeError(f"P446AF {name}: expected 1 anchor, found {n}")
    return s.replace(a,b,1)

TX_CB=r'''static void a52ac_gsi_tx_cb(void *arg)
{
	struct msm_gpi_dma_async_tx_cb_param *cb=arg;
	struct geni_i2c_dev *gi2c;

	if (unlikely(IS_ERR_OR_NULL(cb) || !virt_addr_valid(cb))) {
		pr_err("P446AF GSI BADPTR field=tx_cb val=%px\n", cb);
		return;
	}
	gi2c=cb->userdata;
	if (unlikely(IS_ERR_OR_NULL(gi2c) || !virt_addr_valid(gi2c))) {
		pr_err("P446AF GSI BADPTR field=tx_cb.userdata val=%px\n", gi2c);
		return;
	}
	dev_info_ratelimited(gi2c->se.dev,
		"P446AF GSI TX_CB length=%u code=%u status=%#x\n",
		cb->length,cb->completion_code,cb->status);
	if (cb->completion_code==MSM_GPI_TCE_UNEXP_ERR)
		gi2c->err=-EIO;
	complete(&gi2c->done);
}
static void a52ac_gsi_rx_cb(void *arg)
{
	struct msm_gpi_dma_async_tx_cb_param *cb=arg;
	struct geni_i2c_dev *gi2c;

	if (unlikely(IS_ERR_OR_NULL(cb) || !virt_addr_valid(cb))) {
		pr_err("P446AF GSI BADPTR field=rx_cb val=%px\n", cb);
		return;
	}
	gi2c=cb->userdata;
	if (unlikely(IS_ERR_OR_NULL(gi2c) || !virt_addr_valid(gi2c))) {
		pr_err("P446AF GSI BADPTR field=rx_cb.userdata val=%px\n", gi2c);
		return;
	}
	dev_info_ratelimited(gi2c->se.dev,
		"P446AF GSI RX_CB length=%u code=%u status=%#x\n",
		cb->length,cb->completion_code,cb->status);
	if (cb->completion_code==MSM_GPI_TCE_UNEXP_ERR)
		gi2c->err=-EIO;
	complete(&gi2c->done);
}
'''

def patch(s):
    if TAG in s: return s
    if "A52_PHASE446AE_GSI_WRAPPER_DMA_V1" not in s:
        raise RuntimeError("P446AF requires complete 446AE mapped GSI transport")
    s=once(s,
        "\tbool a52ad_gsi_ready;",
        "\tbool a52ad_gsi_ready;\n"
        "\t/* A52_PHASE446AF_GPI_CALLBACK_ABI_FIX_V1 */\n"
        "\tstruct msm_gpi_dma_async_tx_cb_param a52af_tx_cb, a52af_rx_cb;",
        "persistent GPI callback parameter structures")
    start=s.index("static void a52ac_gsi_tx_cb(void *arg)")
    end=s.index("static void a52ac_gsi_release(void *arg)", start)
    old=s[start:end]
    if old.count("complete(&gi2c->done)")!=2 or \
       old.count("struct geni_i2c_dev *gi2c=arg;")!=2:
        raise RuntimeError("P446AF callback ABI anchors drifted")
    s=s[:start]+TX_CB+s[end:]
    s=once(s,
        "\tgi2c->a52ad_gsi_ready = true;",
        "\tgi2c->a52af_tx_cb.userdata=gi2c;\n"
        "\tgi2c->a52af_rx_cb.userdata=gi2c;\n"
        "\tgi2c->a52ad_gsi_ready = true;",
        "initialize typed callback userdata during provider bind")
    s=once(s,
        "\t\t\tif (IS_ERR_OR_NULL(rd)) {",
        "\t\t\tdev_info(dev,\"P446AF GSI RX_PREP chan=%px tre=%px sg=%px len=%u\\n\",\n"
        "\t\t\t\tgi2c->a52ac_rx,&data,&rxsg,msg->len);\n"
        "\t\t\tif (IS_ERR_OR_NULL(rd) || !virt_addr_valid(rd)) {",
        "validate and log prepared RX descriptor")
    s=once(s,
        "\t\t\t\tdev_err(dev, \"P446AD GSI RX descriptor invalid rc=%d\\n\", ret);",
        "\t\t\t\tdev_err(dev, \"P446AD GSI RX descriptor invalid rc=%d\\n\", ret);\n"
        "\t\t\t\tdev_err(dev, \"P446AF GSI BADPTR field=rx_desc val=%px\\n\",rd);",
        "log invalid rx descriptor")
    s=once(s,
        "\t\t\trd->callback_param=gi2c;",
        "\t\t\trd->callback_param=&gi2c->a52af_rx_cb;",
        "fix corrupting RX callback parameter")
    s=once(s,
        "\t\t\ttd->callback_param=gi2c;",
        "\t\t\ttd->callback_param=&gi2c->a52af_tx_cb;",
        "fix corrupting TX callback parameter")
    s=once(s,
        "\t\tif (IS_ERR_OR_NULL(td)) {",
        "\t\tif (IS_ERR_OR_NULL(td) || !virt_addr_valid(td)) {",
        "validate TX descriptor")
    s=once(s,
        "\t\t\tdev_err(dev, \"P446AD GSI TX descriptor invalid rc=%d\\n\", ret);",
        "\t\t\tdev_err(dev, \"P446AD GSI TX descriptor invalid rc=%d\\n\", ret);\n"
        "\t\t\tdev_err(dev, \"P446AF GSI BADPTR field=tx_desc val=%px\\n\",td);",
        "log invalid TX descriptor")
    s=once(s,
        "\t\tgi2c->cur=msg;",
        """		gi2c->cur=msg;
		if (unlikely(IS_ERR_OR_NULL(gi2c->a52ac_tx) ||
			     !virt_addr_valid(gi2c->a52ac_tx) ||
			     IS_ERR_OR_NULL(gi2c->a52ac_rx) ||
			     !virt_addr_valid(gi2c->a52ac_rx))) {
			dev_err(dev, "P446AF GSI BADPTR field=channel tx=%px rx=%px\\n",
				gi2c->a52ac_tx, gi2c->a52ac_rx);
			ret=-EIO;
			break;
		}
		if (unlikely(gi2c->a52ac_tx->device != gi2c->a52ae_tx_ops ||
			     gi2c->a52ac_rx->device != gi2c->a52ae_rx_ops)) {
			dev_err(dev, "P446AF GSI BADPTR field=chan.device tx=%px rx=%px\\n",
				gi2c->a52ac_tx->device,gi2c->a52ac_rx->device);
			ret=-EIO;
			break;
		}""",
        "validate RX/TX channel pointer and dma_device ownership per message")
    # Use an explicit cookie witness on RX submit, before issue_pending.
    s=once(s,
        "\t\t\tret=dma_submit_error(dmaengine_submit(rd));\n"
        "\t\t\tif (ret) goto cleanup;\n"
        "\t\t\tdma_async_issue_pending(gi2c->a52ac_rx);",
        """			{
				dma_cookie_t cookie=dmaengine_submit(rd);
				ret=dma_submit_error(cookie);
				dev_info(dev,
					"P446AF GSI RX_SUBMIT desc=%px cookie=%d rc=%d\\n",
					rd,cookie,ret);
				if (ret) goto cleanup;
			}
			dma_async_issue_pending(gi2c->a52ac_rx);""",
        "capture RX cookie and submit status")
    s=once(s,
        "\t\tret=dma_submit_error(dmaengine_submit(td));\n"
        "\t\tif (ret)goto cleanup;",
        """		{
			dma_cookie_t cookie=dmaengine_submit(td);
			ret=dma_submit_error(cookie);
			dev_info_ratelimited(dev,
				"P446AF GSI TX_SUBMIT desc=%px cookie=%d rc=%d\\n",
				td,cookie,ret);
			if (ret)goto cleanup;
		}""",
        "capture TX cookie")
    return s

def patch_touch(s):
    if "A52_PHASE446AF_TOUCH_PROBE_FAILFAST_V1" in s:
        return s
    if "A52_PHASE446AD_TOUCH_PROBE_DELAY_V1" not in s:
        raise RuntimeError("P446AF touch fail-fast needs 446AD touch delay")
    start=s.index("int fts_read_reg(")
    end=s.index("#ifdef FTS_SUPPORT_SPONGELIB",start)
    frag=s[start:end]
    frag=once(frag,
        "\tint retry = FTS_TS_I2C_RETRY_CNT;",
        "\t/* A52_PHASE446AF_TOUCH_PROBE_FAILFAST_V1 */\n"
        "\tint retry = info->probe_done ? FTS_TS_I2C_RETRY_CNT : 1;",
        "single attempt for probe-only reads")
    s=s[:start]+frag+s[end:]
    s=once(s,
        "\trc = fts_read_reg(info, &regAdd[0], 1, (u8 *)data, FTS_EVENT_SIZE);\n"
        "\tif (rc == -ENOTCONN) {\n"
        "\t\treturn rc;\n"
        "\t}",
        "\trc = fts_read_reg(info, &regAdd[0], 1, (u8 *)data, FTS_EVENT_SIZE);\n"
        "\tif (rc < 0) {\n"
        "\t\tinput_err(true, &info->client->dev,\n"
        '\t\t\t"P446AF TOUCH INIT_ABORT first_read rc=%d\\n", rc);\n'
        "\t\treturn rc;\n"
        "\t}",
        "abort STM init on first I2C read failure")
    return s


def check(root):
    s=(root/I2C).read_text()
    t=(root/TOUCH).read_text()
    for key in ("A52_PHASE446AF_TOUCH_PROBE_FAILFAST_V1",
                "int retry = info->probe_done ? FTS_TS_I2C_RETRY_CNT : 1;",
                "P446AF TOUCH INIT_ABORT first_read rc=%d",
                "A52_PHASE446AD_TOUCH_PROBE_DELAY_V1"):
        if key not in t:
            raise RuntimeError("P446AF touch fail-fast missing "+key)
    required=(TAG,"a52af_tx_cb.userdata=gi2c",
        "a52af_rx_cb.userdata=gi2c",
        "rd->callback_param=&gi2c->a52af_rx_cb",
        "td->callback_param=&gi2c->a52af_tx_cb",
        "P446AF GSI RX_SUBMIT","P446AF GSI RX_PREP",
        "P446AF GSI BADPTR field=channel",
        "P446AE DMA_MAP","a52ae_wrapper_bind(gi2c)")
    for x in required:
        if x not in s:raise RuntimeError("P446AF missing "+x)
    f=s[s.index("static int a52ac_gsi_xfer("):
        s.index("static int geni_i2c_xfer(",s.index("static int a52ac_gsi_xfer("))]
    if "callback_param=gi2c;" in f or "callback_param=&gi2c->a52af_" not in f:
        raise RuntimeError("P446AF unsafe GPI callback parameter remains")
    # Source parity with pinned TG gpi.c: its immediate-data completion writes
    # length/status/code/imed_tre into descriptor->callback_param.
    print("P446AF GPI CALLBACK ABI PASS: stable TX/RX cb_param structs + userdata, both DMA channels guarded")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    args=ap.parse_args()
    p=args.root/I2C
    if not args.check_only:
        touch_path=args.root/TOUCH
        pending=[(p,patch(p.read_text())),
                 (touch_path,patch_touch(touch_path.read_text()))]
        for file,new_content in pending:
            file.write_text(new_content)
    check(args.root)

if __name__=="__main__":main()
