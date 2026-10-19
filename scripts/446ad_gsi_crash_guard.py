#!/usr/bin/env python3
"""446AD GSI crash containment after 446AC, without touching pinned TG GPI blobs.

The installed DMA provider is mandatory before registering the SE8 adapter.
The asynchronous FTS probe is delayed by 8 s for serial-console diagnostics.
All changes use single-match anchors and are strictly rechecked.
"""
from pathlib import Path
import argparse

I2C = Path("drivers/i2c/busses/i2c-qcom-geni.c")
TOUCH = Path("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c")
TAG = "A52_PHASE446AD_GSI_FIRST_XFER_GUARD_V1"
DELAY = "A52_PHASE446AD_TOUCH_PROBE_DELAY_V1"


def once(src, before, after, label):
    count = src.count(before)
    if count != 1:
        raise RuntimeError(f"P446AD {label}: expected one anchor, got {count}")
    return src.replace(before, after, 1)


CHANNELS = r'''/* A52_PHASE446AD_GSI_FIRST_XFER_GUARD_V1
 * DMA provider readiness is an I2C probe prerequisite, not an FTS xfer task.
 * TG GPI uses five cells: channel, SE ID, protocol, ring size, priority.
 */
static int a52ac_gsi_channels(struct geni_i2c_dev *gi2c)
{
	struct device *dev = gi2c->se.dev;
	struct of_phandle_args txspec, rxspec;
	struct dma_chan *tx, *rx;
	int ret, tx_idx, rx_idx;

	if (!IS_ERR_OR_NULL(gi2c->a52ac_tx) &&
	    !IS_ERR_OR_NULL(gi2c->a52ac_rx))
		return 0;
	if (!dev->of_node ||
	    !of_find_property(dev->of_node, "dmas", NULL) ||
	    !of_find_property(dev->of_node, "dma-names", NULL))
		return dev_err_probe(dev, -EINVAL,
				     "P446AD GSI missing dmas/dma-names\n");
	tx_idx = of_property_match_string(dev->of_node, "dma-names", "tx");
	rx_idx = of_property_match_string(dev->of_node, "dma-names", "rx");
	if (tx_idx < 0 || rx_idx < 0 || tx_idx == rx_idx)
		return dev_err_probe(dev, -EINVAL,
				     "P446AD GSI invalid dma-names tx=%d rx=%d\n",
				     tx_idx, rx_idx);
	ret = of_parse_phandle_with_args(dev->of_node, "dmas", "#dma-cells",
					 tx_idx, &txspec);
	if (ret)
		return dev_err_probe(dev, ret,
				     "P446AD GSI TX spec invalid\n");
	ret = of_parse_phandle_with_args(dev->of_node, "dmas", "#dma-cells",
					 rx_idx, &rxspec);
	if (ret) {
		of_node_put(txspec.np);
		return dev_err_probe(dev, ret,
				     "P446AD GSI RX spec invalid\n");
	}
	if (txspec.np != rxspec.np || txspec.args_count != 5 ||
	    rxspec.args_count != 5 || txspec.args[0] != 0 ||
	    rxspec.args[0] != 1 || !of_device_is_available(txspec.np)) {
		dev_err(dev, "P446AD GSI invalid provider/schema tx=%d rx=%d chid=%u/%u\n",
			txspec.args_count, rxspec.args_count, txspec.args[0],
			rxspec.args[0]);
		of_node_put(txspec.np);
		of_node_put(rxspec.np);
		return -EINVAL;
	}
	dev_info(dev, "P446AD GSI BIND provider=%pOF seid=%u cells=5\n",
		 txspec.np, txspec.args[1]);
	of_node_put(txspec.np);
	of_node_put(rxspec.np);

	tx = dma_request_chan(dev, "tx");
	if (IS_ERR_OR_NULL(tx)) {
		ret = IS_ERR(tx) ? PTR_ERR(tx) : -EPROBE_DEFER;
		dev_warn(dev, "P446AD GSI chan tx=%pe rx=NULL rc=%d stage=request_tx\n",
			 tx, ret);
		return ret;
	}
	gi2c->a52ac_tx = tx;
	gi2c->a52ac_tx_ev.init.callback = a52ac_gsi_event;
	gi2c->a52ac_tx_ev.init.cb_param = gi2c;
	gi2c->a52ac_tx_ev.cmd = MSM_GPI_INIT;
	tx->private = &gi2c->a52ac_tx_ev;

	rx = dma_request_chan(dev, "rx");
	if (IS_ERR_OR_NULL(rx)) {
		ret = IS_ERR(rx) ? PTR_ERR(rx) : -EPROBE_DEFER;
		dev_warn(dev, "P446AD GSI chan tx=%s rx=%pe rc=%d stage=request_rx\n",
			 dma_chan_name(tx), rx, ret);
		goto error;
	}
	gi2c->a52ac_rx = rx;
	gi2c->a52ac_rx_ev.init.callback = a52ac_gsi_event;
	gi2c->a52ac_rx_ev.init.cb_param = gi2c;
	gi2c->a52ac_rx_ev.cmd = MSM_GPI_INIT;
	rx->private = &gi2c->a52ac_rx_ev;

	ret = dmaengine_slave_config(tx, NULL);
	if (ret) {
		dev_err(dev, "P446AD GSI chan tx=%s rx=%s rc=%d stage=config_tx\n",
			dma_chan_name(tx), dma_chan_name(rx), ret);
		goto error;
	}
	ret = dmaengine_slave_config(rx, NULL);
	if (ret) {
		dev_err(dev, "P446AD GSI chan tx=%s rx=%s rc=%d stage=config_rx\n",
			dma_chan_name(tx), dma_chan_name(rx), ret);
		goto error;
	}
	dev_info(dev, "P446AD GPI READY tx=%s rx=%s ev_ring=initialized rc=0\n",
		 dma_chan_name(tx), dma_chan_name(rx));
	return 0;
error:
	a52ac_gsi_release(gi2c);
	return ret;
}
'''


def apply_i2c(s):
    if TAG in s:
        return s
    if "A52_PHASE446AC_SE8_GSI_I2C_V1" not in s:
        raise RuntimeError("P446AD requires installed 446AC GSI path")
    start = s.index("static int a52ac_gsi_channels(")
    end = s.index("static int a52ac_gsi_xfer(", start)
    if s[start:end].count("static int a52ac_gsi_channels(") != 1:
        raise RuntimeError("P446AD GSI channel block changed")
    s = s[:start] + CHANNELS + s[end:]
    s = once(s,
        "\tret=a52ac_gsi_channels(gi2c);\n\tif (ret)\n\t\treturn ret;\n\ta52ac_select_gsi_mode(gi2c);",
        """	if (IS_ERR_OR_NULL(gi2c->a52ac_tx) ||
	    IS_ERR_OR_NULL(gi2c->a52ac_rx)) {
		dev_err(dev, "P446AD I2C GSI first_xfer rc=%d channels absent\\n", -EIO);
		return -EIO;
	}
	if (!clk) {
		dev_err(dev, "P446AD I2C GSI first_xfer rc=%d clock timing absent\\n", -EIO);
		return -EIO;
	}
	a52ac_select_gsi_mode(gi2c);""".replace("\\n", "\\n"),
        "transfer channel guard")
    # Strongest ordering: bind + GPI ring configuration before publishing the I2C adapter.
    s = once(s,
        '\tif (gi2c->a52ac_gsi)\n\t\tdev_info(dev,"P446AC GSI_ONLY SE8 fifo_disabled=1 transport=TG_GPI\\n");',
        '''	if (gi2c->a52ac_gsi) {
		dev_info(dev, "P446AC GSI_ONLY SE8 fifo_disabled=1 transport=TG_GPI\\n");
		ret = devm_add_action_or_reset(dev, a52ac_gsi_release, gi2c);
		if (ret)
			return ret;
		ret = a52ac_gsi_channels(gi2c);
		if (ret)
			return dev_err_probe(dev, ret,
					     "P446AD GSI provider not ready\\n");
	}''',
        "probe ordering")
    s = once(s,
        '''	if (gi2c->a52ac_gsi) {
		ret=devm_add_action_or_reset(dev,a52ac_gsi_release,gi2c);
		if (ret)return ret;
	}
	dev_dbg(dev, "Geni-I2C adaptor successfully added\\n");''',
        '''	dev_dbg(dev, "Geni-I2C adaptor successfully added\\n");''',
        "remove late channel cleanup")
    s = once(s,
        "\t\tif (!rd) {ret=-ENOMEM;goto cleanup;}",
        """			if (IS_ERR_OR_NULL(rd)) {
				ret = IS_ERR(rd) ? PTR_ERR(rd) : -EIO;
				dev_err(dev, "P446AD GSI RX descriptor invalid rc=%d\\n", ret);
				goto cleanup;
			}""",
        "RX descriptor guard")
    s = once(s,
        "\t\tif (!td) {ret=-ENOMEM;goto cleanup;}",
        """		if (IS_ERR_OR_NULL(td)) {
			ret = IS_ERR(td) ? PTR_ERR(td) : -EIO;
			dev_err(dev, "P446AD GSI TX descriptor invalid rc=%d\\n", ret);
			goto cleanup;
		}""",
        "TX descriptor guard")
    s = once(s,
        "\t\tif (dma_mapping_error(mapdev,phys)) {",
        '''		if (dma_mapping_error(mapdev,phys)) {
			dev_err(dev, "P446AD GSI map error rc=%d\\n", -EIO);''',
        "DMA map diagnostic")
    s = once(s,
        '''		dev_info_ratelimited(dev,"P446AC GSI XFER_DONE msg=%d/%d rc=%d\\n",i+1,num,ret);''',
        '''		if (i == 0)
			dev_info(dev, "P446AD I2C GSI first_xfer rc=%d dir=%c len=%u\\n",
				 ret, rdmsg ? 'R' : 'W', msg->len);
		dev_info_ratelimited(dev,"P446AC GSI XFER_DONE msg=%d/%d rc=%d\\n",i+1,num,ret);''',
        "first transfer result witness")
    return s


def apply_touch(s):
    if DELAY in s:
        return s
    s = once(s,
        'early_param("a52.touch",a52x_touch_param);',
        '''/* A52_PHASE446AD_TOUCH_PROBE_DELAY_V1: diagnostic only, no firmware writes. */
static unsigned int a52ad_touch_delay_ms = 8000;
static int __init a52ad_touch_delay_param(char *value)
{
	unsigned int ms;
	if (!value || kstrtouint(value, 0, &ms) || ms > 30000)
		return -EINVAL;
	a52ad_touch_delay_ms = ms;
	return 0;
}
early_param("a52.touch_delay_ms", a52ad_touch_delay_param);
early_param("a52.touch",a52x_touch_param);''',
        "early-boot delay parameter")
    s = once(s,
        'pr_info("P446X TOUCH probe-enter bus=%d addr=%#x irq=%d pid=%d comm=%s\\n",',
        '''pr_info("P446AD TOUCH PROBE_DELAY delay_ms=%u, before any STM I2C\\n",
		a52ad_touch_delay_ms);
	if (a52ad_touch_delay_ms)
		msleep(a52ad_touch_delay_ms);
	pr_info("P446X TOUCH probe-enter bus=%d addr=%#x irq=%d pid=%d comm=%s\\n",''',
        "touch probe delay")
    return s


def verify(root):
    c = (root/I2C).read_text()
    t = (root/TOUCH).read_text()
    required_c = (
        TAG, "dma_request_chan(dev, \"tx\")", "dma_request_chan(dev, \"rx\")",
        "P446AD GPI READY", "P446AD I2C GSI first_xfer",
        "P446AD GSI provider not ready", "dma_mapping_error(mapdev,phys)",
        "IS_ERR_OR_NULL(rd)", "IS_ERR_OR_NULL(td)",
    )
    required_t = (DELAY, 'early_param("a52.touch_delay_ms"', "msleep(a52ad_touch_delay_ms)",
                  "a52ad_touch_delay_ms = 8000", "P446X TOUCH FW")
    for key in required_c:
        if key not in c:
            raise RuntimeError(f"P446AD missing I2C contract {key}")
    for key in required_t:
        if key not in t:
            raise RuntimeError(f"P446AD missing touch contract {key}")
    if c.count("devm_add_action_or_reset(dev, a52ac_gsi_release, gi2c)") != 1:
        raise RuntimeError("P446AD channel cleanup ownership mismatch")
    if c.count("ret = a52ac_gsi_channels(gi2c);") != 1:
        raise RuntimeError("P446AD channel acquisition must occur once at probe")
    print("P446AD GSI GUARD PASS: probe-ordered 5-cell DMA, error-checked RX/TX and first xfer")
    print("P446AD TOUCH DELAY PASS: early parameter default 8000ms, async probe and kill switch kept")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not args.check_only:
        paths = [(args.root/I2C, apply_i2c), (args.root/TOUCH, apply_touch)]
        pending = [(f, fn(f.read_text())) for f,fn in paths]
        for f, updated in pending:
            f.write_text(updated)
    verify(args.root)


if __name__ == "__main__":
    main()
