#!/usr/bin/env python3
"""446AE: correct SE8 GSI DMA mapping ownership and contain GPI error cleanup.

Runs after P446AD against the SHA-pinned Phase446S restored kernel.  Touch
firmware, non-SE8 I2C, GPI DMA ring allocations, DRM, USB, and KSU are untouched.
"""
import argparse
from pathlib import Path

I2C = Path("drivers/i2c/busses/i2c-qcom-geni.c")
TAG = "A52_PHASE446AE_GSI_WRAPPER_DMA_V1"


def once(s, old, new, label):
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"P446AE {label}: expected one anchor, found {count}")
    return s.replace(old, new, 1)


HELPERS = r'''/* A52_PHASE446AE_GSI_WRAPPER_DMA_V1
 * The SE8 I2C device has no QUP IOMMU SID.  Its wrapper is SID 0x4c3.
 * GPI ring allocations remain under the DMA provider device (SID 0x4d6).
 */
static void a52ae_wrapper_put(void *arg)
{
	put_device((struct device *)arg);
}

static int a52ae_wrapper_bind(struct geni_i2c_dev *gi2c)
{
	struct device *dev = gi2c->se.dev;
	struct device_node *np;
	struct platform_device *wrapper;
	struct iommu_domain *domain;
	struct resource res;
	u32 pool[2];
	int ret;

	if (gi2c->a52ae_wrapper_dev)
		return 0;
	np = of_parse_phandle(dev->of_node, "qcom,wrapper-core", 0);
	if (!np)
		return dev_err_probe(dev, -EINVAL,
				     "P446AE missing SE8 wrapper phandle\n");
	ret = of_address_to_resource(np, 0, &res);
	if (ret || res.start != 0x009c0000) {
		of_node_put(np);
		return dev_err_probe(dev, -EINVAL,
				     "P446AE wrong wrapper address\n");
	}
	ret = of_property_read_u32_array(np, "qcom,iommu-dma-addr-pool",
					 pool, 2);
	if (ret || pool[0] != 0x40000000 || pool[1] != 0xc0000000) {
		dev_err(dev, "P446AE wrapper pool unexpected rc=%d base=%#x size=%#x\n",
			ret, ret ? 0 : pool[0], ret ? 0 : pool[1]);
		of_node_put(np);
		return -EINVAL;
	}
	wrapper = of_find_device_by_node(np);
	of_node_put(np);
	if (!wrapper)
		return dev_err_probe(dev, -EPROBE_DEFER,
				     "P446AE QUP wrapper not registered\n");
	domain = iommu_get_domain_for_dev(&wrapper->dev);
	dev_info(dev, "P446AE WRAPPER dev=%s iommu_domain=%p type=%d pool=%#x/%#x\n",
		 dev_name(&wrapper->dev), domain, domain ? domain->type : -1,
		 pool[0], pool[1]);
	if (!domain || domain->type != IOMMU_DOMAIN_DMA) {
		put_device(&wrapper->dev);
		return dev_err_probe(dev, -EPROBE_DEFER,
				     "P446AE QUP wrapper lacks DMA IOMMU domain\n");
	}
	ret = devm_add_action_or_reset(dev, a52ae_wrapper_put, &wrapper->dev);
	if (ret)
		return ret; /* _or_reset releases the platform-device ref */
	gi2c->a52ae_wrapper_dev = &wrapper->dev;
	gi2c->a52ae_tx_ops = gi2c->a52ac_tx->device;
	gi2c->a52ae_rx_ops = gi2c->a52ac_rx->device;
	dev_info(dev, "P446AE DMA_OWNER dev=%s gpi_tx=%s gpi_rx=%s\n",
		 dev_name(gi2c->a52ae_wrapper_dev),
		 dma_chan_name(gi2c->a52ac_tx),
		 dma_chan_name(gi2c->a52ac_rx));
	return 0;
}
'''


def patch(s):
    if TAG in s:
        return s
    if "A52_PHASE446AD_GSI_FIRST_XFER_GUARD_V1" not in s:
        raise RuntimeError("P446AE requires completed 446AD GSI guard")
    s = "#include <linux/iommu.h>\n#include <linux/of_platform.h>\n" + s
    s = once(s, "\tbool a52ad_gsi_ready;",
        "\tbool a52ad_gsi_ready;\n"
        "\tstruct device *a52ae_wrapper_dev;\n"
        "\tstruct dma_device *a52ae_tx_ops, *a52ae_rx_ops;\n"
        "\tu32 a52ae_gsi_status, a52ae_gsi_code;",
        "add wrapper ownership and GPI status")
    s = once(s,
        "\tif (cb->cb_event==MSM_GPI_QUP_NOTIFY) {",
        "\tgi2c->a52ae_gsi_status=cb->status;\n"
        "\tgi2c->a52ae_gsi_code=cb->error_log.error_code;\n"
        "\tif (cb->cb_event==MSM_GPI_QUP_NOTIFY) {",
        "store GPI error status")
    s = once(s,
        "static int a52ac_gsi_xfer(struct geni_i2c_dev *gi2c,",
        HELPERS + "\nstatic int a52ac_gsi_xfer(struct geni_i2c_dev *gi2c,",
        "install guarded wrapper binding")
    s = once(s,
        "\tstruct device *mapdev=dev->parent ? dev->parent : dev;",
        "\tstruct device *mapdev=gi2c->a52ae_wrapper_dev;",
        "map via QUPv3_1 wrapper, never child/parent fallback")
    s = once(s,
        "\tif (IS_ERR_OR_NULL(gi2c->a52ac_tx) ||\n"
        "\t    IS_ERR_OR_NULL(gi2c->a52ac_rx)) {",
        "\tif (!mapdev || !iommu_get_domain_for_dev(mapdev)) {\n"
        "\t\tdev_err(dev, \"P446AE DMA_OWNER missing mapping domain rc=%d\\n\",-EIO);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tif (IS_ERR_OR_NULL(gi2c->a52ac_tx) ||\n"
        "\t    IS_ERR_OR_NULL(gi2c->a52ac_rx)) {",
        "ensure DMA-mapping device before touching hardware")
    s = once(s,
        "\t\tgi2c->err=0;\n\t\treinit_completion(&gi2c->done);",
        "\t\tgi2c->err=0;\n"
        "\t\tgi2c->a52ae_gsi_status=0;\n"
        "\t\tgi2c->a52ae_gsi_code=0;\n"
        "\t\treinit_completion(&gi2c->done);",
        "reset error context per message")
    s = once(s,
        "\t\tif (dma_mapping_error(mapdev,phys)) {\n"
        "\t\t\tdev_err(dev, \"P446AD GSI map error rc=%d\\n\", -EIO);",
        "\t\tif (dma_mapping_error(mapdev,phys)) {\n"
        "\t\t\tdev_err(dev,\"P446AE DMA_MAP dev=%s dir=%s iova=%pad len=%u rc=%d\\n\",\n"
        "\t\t\t\tdev_name(mapdev),rdmsg ? \"rx\" : \"tx\",&phys,msg->len,-EIO);\n"
        "\t\t\tdev_err(dev, \"P446AD GSI map error rc=%d\\n\", -EIO);",
        "record failed mapping device")
    s = once(s,
        "\t\tcfg.dword[0]=MSM_GPI_I2C_CONFIG0_TRE_DWORD0(3,",
        """		/* The old child/parent mapping returned SWIOTLB PA 0xefc00000.
		 * Refuse any such address even if it falls inside the IOVA
		 * numerical pool, which alone does not prove an IOMMU mapping.
		 */
		if (upper_32_bits(phys) || lower_32_bits(phys) < 0x40000000 ||
		    (phys >= 0xefc00000ULL && phys < 0xf0000000ULL) ||
		    msg->len > (0x100000000ULL - phys)) {
			dev_err(dev,"P446AE DMA_MAP dev=%s dir=%s iova=%pad len=%u rc=%d invalid IOVA\\n",
				dev_name(mapdev),rdmsg ? "rx" : "tx",&phys,msg->len,-EIO);
			dma_unmap_single(mapdev,phys,msg->len,
				rdmsg ? DMA_FROM_DEVICE : DMA_TO_DEVICE);
			i2c_put_dma_safe_msg_buf(buf,msg,false);
			ret=-EIO;
			break;
		}
		dev_info_ratelimited(dev,"P446AE DMA_MAP dev=%s dir=%s iova=%pad len=%u rc=0\\n",
			dev_name(mapdev),rdmsg ? "rx" : "tx",&phys,msg->len);
		cfg.dword[0]=MSM_GPI_I2C_CONFIG0_TRE_DWORD0(3,""",
        "strict IOVA/swiotlb filtering")
    s = once(s,
        "\t\tif (ret) {\n"
        "\t\t\tdmaengine_terminate_all(gi2c->a52ac_tx);\n"
        "\t\t\tdmaengine_terminate_all(gi2c->a52ac_rx);\n"
        "\t\t}\n"
        "\t\tdma_unmap_single(mapdev,phys,msg->len,",
        """		if (ret) {
			int stop_rc=-EIO;
			/* The TG GPI I2C terminate op STOP/RESET/STARTs both
			 * channels as one GPII. Never terminate the pair twice.
			 * Do not dereference a possibly damaged dma_device.
			 */
			if (gi2c->a52ad_gsi_ready &&
			    !IS_ERR_OR_NULL(gi2c->a52ac_tx) &&
			    gi2c->a52ac_tx->device == gi2c->a52ae_tx_ops &&
			    !IS_ERR_OR_NULL(gi2c->a52ac_rx) &&
			    gi2c->a52ac_rx->device == gi2c->a52ae_rx_ops)
				stop_rc=dmaengine_terminate_sync(gi2c->a52ac_tx);
			if (stop_rc)
				dev_err(dev,"P446AE GSI ERR status=%#x code=%#x cleanup=terminate_failed rc=%d\\n",
					gi2c->a52ae_gsi_status,gi2c->a52ae_gsi_code,stop_rc);
			else
				dev_info(dev,"P446AE GSI ERR status=%#x code=%#x cleanup=ok rc=%d\\n",
					gi2c->a52ae_gsi_status,gi2c->a52ae_gsi_code,ret);
		}
		dma_unmap_single(mapdev,phys,msg->len,""",
        "single-channel synchronous GPII group reset and cleanup")
    s = once(s,
        '\t\tret = a52ac_gsi_channels(gi2c);\n'
        '\t\tif (ret)\n'
        '\t\t\treturn dev_err_probe(dev, ret,\n'
        '\t\t\t\t\t     "P446AD GSI provider not ready\\n");',
        '\t\tret = a52ac_gsi_channels(gi2c);\n'
        '\t\tif (ret)\n'
        '\t\t\treturn dev_err_probe(dev, ret,\n'
        '\t\t\t\t\t     "P446AD GSI provider not ready\\n");\n'
        '\t\tret=a52ae_wrapper_bind(gi2c);\n'
        '\t\tif (ret)\n'
        '\t\t\treturn ret;',
        "wrapper IOMMU bind before registering I2C adapter")
    return s


def check(root):
    c=(root/I2C).read_text()
    required=(TAG,"P446AE DMA_MAP dev=%s","P446AE GSI ERR",
              "a52ae_wrapper_bind(gi2c)","iommu_get_domain_for_dev",
              "of_find_device_by_node", "dmaengine_terminate_sync",
              "0xefc00000ULL", "P446AD GSI BIND",
              "P446AD I2C GSI first_xfer")
    for value in required:
        if value not in c:
            raise RuntimeError("P446AE missing source contract "+value)
    if "struct device *mapdev=dev->parent ? dev->parent : dev;" in c:
        raise RuntimeError("P446AE unsafe parent DMA mapping remains")
    if c.count("ret=a52ae_wrapper_bind(gi2c);") != 1:
        raise RuntimeError("P446AE wrapper must be acquired once at probe")
    if c.count("dmaengine_terminate_sync(gi2c->a52ac_tx)") != 1:
        raise RuntimeError("P446AE unexpected GPII reset count")
    if "dmaengine_terminate_all(gi2c->a52ac_rx)" in c:
        raise RuntimeError("P446AE never reset TX and RX separately")
    print("P446AE DMA_OWNER PASS: QUPv3_1 IOMMU device, pool/swiotlb guard, synchronous single GPII reset")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    args=p.parse_args()
    path=args.root/I2C
    if not args.check_only:
        path.write_text(patch(path.read_text()))
    check(args.root)


if __name__=="__main__":
    main()
