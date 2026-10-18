#!/usr/bin/env python3
"""446AC: add TG-compatible GSI DMA I2C TRE path to ACK 5.10 only for SE8.

All other controllers retain the existing native FIFO/DMA path.
Requires TG GPI provider + include/linux/msm_gpi.h pinned to 6bf351bd.
"""
import argparse
from pathlib import Path
PATH=Path("drivers/i2c/busses/i2c-qcom-geni.c")
TAG="A52_PHASE446AC_SE8_GSI_I2C_V1"
def one(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError("P446AC GSI "+name+" anchor count="+str(n))
    return s.replace(a,b,1)
GSI=r'''
/* A52_PHASE446AC_SE8_GSI_I2C_V1
 * For GENI_IF_FIFO_DISABLE_RO=1 the QUP firmware exposes only GSI.
 * Generate TG-compatible CONFIG0/GO/DATA TREs through its GPI DMAengine.
 * Keep the stock ACK FIFO path untouched for all other engines.
 */
static void a52ac_gsi_event(struct dma_chan *ch,
			   const struct msm_gpi_cb *cb, void *arg)
{
	struct geni_i2c_dev *gi2c=arg;
	if (cb->cb_event==MSM_GPI_QUP_NOTIFY) {
		if (cb->status & M_GP_IRQ_1_EN)
			gi2c->err=-ENXIO;
		else if (cb->status & M_GP_IRQ_3_EN)
			gi2c->err=-EPROTO;
		else if (cb->status & M_GP_IRQ_4_EN)
			gi2c->err=-EAGAIN;
	} else {
		gi2c->err=-EIO;
		dev_err(gi2c->se.dev,
			"P446AC GSI EVENT event=%d status=%#x code=%#x\n",
			cb->cb_event,cb->status,cb->error_log.error_code);
	}
	if (gi2c->err)
		complete(&gi2c->done);
}
static void a52ac_gsi_tx_cb(void *arg)
{
	struct geni_i2c_dev *gi2c=arg;
	complete(&gi2c->done);
}
static void a52ac_gsi_rx_cb(void *arg)
{
	struct geni_i2c_dev *gi2c=arg;
	complete(&gi2c->done);
}
static void a52ac_gsi_release(void *arg)
{
	struct geni_i2c_dev *gi2c=arg;
	if (gi2c->a52ac_tx) {
		dmaengine_terminate_all(gi2c->a52ac_tx);
		dma_release_channel(gi2c->a52ac_tx);
		gi2c->a52ac_tx=NULL;
	}
	if (gi2c->a52ac_rx) {
		dmaengine_terminate_all(gi2c->a52ac_rx);
		dma_release_channel(gi2c->a52ac_rx);
		gi2c->a52ac_rx=NULL;
	}
}
static int a52ac_gsi_channels(struct geni_i2c_dev *gi2c)
{
	struct device *dev=gi2c->se.dev;
	int ret;
	if (gi2c->a52ac_tx && gi2c->a52ac_rx)
		return 0;
	if (!of_find_property(dev->of_node,"dmas",NULL) ||
	    !of_find_property(dev->of_node,"dma-names",NULL)) {
		dev_err(dev,"P446AC GSI missing SE8 dmas/dma-names DT bindings\n");
		return -ENODEV;
	}
	gi2c->a52ac_tx=dma_request_slave_channel(dev,"tx");
	if (!gi2c->a52ac_tx) {
		dev_err(dev,"P446AC GSI TX DMA channel unavailable\n");
		return -ENODEV;
	}
	gi2c->a52ac_tx_ev.init.callback=a52ac_gsi_event;
	gi2c->a52ac_tx_ev.init.cb_param=gi2c;
	gi2c->a52ac_tx_ev.cmd=MSM_GPI_INIT;
	gi2c->a52ac_tx->private=&gi2c->a52ac_tx_ev;
	ret=dmaengine_slave_config(gi2c->a52ac_tx,NULL);
	if (ret)
		goto err;
	gi2c->a52ac_rx=dma_request_slave_channel(dev,"rx");
	if (!gi2c->a52ac_rx) {
		dev_err(dev,"P446AC GSI RX DMA channel unavailable\n");
		ret=-ENODEV;
		goto err;
	}
	gi2c->a52ac_rx_ev.init.callback=a52ac_gsi_event;
	gi2c->a52ac_rx_ev.init.cb_param=gi2c;
	gi2c->a52ac_rx_ev.cmd=MSM_GPI_INIT;
	gi2c->a52ac_rx->private=&gi2c->a52ac_rx_ev;
	ret=dmaengine_slave_config(gi2c->a52ac_rx,NULL);
	if (ret)
		goto err;
	dev_info(dev,"P446AC GSI CHANNELS tx=%s rx=%s rc=0\n",
		 dma_chan_name(gi2c->a52ac_tx),dma_chan_name(gi2c->a52ac_rx));
	return 0;
err:
	a52ac_gsi_release(gi2c);
	dev_err(dev,"P446AC GSI CHANNEL_INIT rc=%d\n",ret);
	return ret;
}
static int a52ac_gsi_xfer(struct geni_i2c_dev *gi2c,
			 struct i2c_msg *msgs,int num)
{
	struct device *dev=gi2c->se.dev;
	struct device *mapdev=dev->parent ? dev->parent : dev;
	const struct geni_i2c_clk_fld *clk=gi2c->clk_fld;
	int i,ret=0;
	ret=a52ac_gsi_channels(gi2c);
	if (ret)
		return ret;
	for (i=0;i<num;i++) {
		struct i2c_msg *msg=&msgs[i];
		struct msm_gpi_tre cfg={},go={},data={};
		struct scatterlist txsg[3],rxsg;
		struct dma_async_tx_descriptor *td,*rd;
		dma_addr_t phys;
		u8 *buf;
		u32 a52ac_stretch=(i<num-1) ? 4 : 0;
		u32 a52ac_op=(msg->flags & I2C_M_RD) ? 2 : 1;
		bool rdmsg=!!(msg->flags & I2C_M_RD);
		int n=0;
		unsigned long timeout;
		gi2c->err=0;
		reinit_completion(&gi2c->done);
		gi2c->cur=msg;
		buf=i2c_get_dma_safe_msg_buf(msg,1);
		if (!buf) {ret=-ENOMEM;break;}
		phys=dma_map_single(mapdev,buf,msg->len,
				    rdmsg ? DMA_FROM_DEVICE : DMA_TO_DEVICE);
		if (dma_mapping_error(mapdev,phys)) {
			i2c_put_dma_safe_msg_buf(buf,msg,false);
			ret=-EIO;
			break;
		}
		cfg.dword[0]=MSM_GPI_I2C_CONFIG0_TRE_DWORD0(3,
				clk->t_cycle_cnt,clk->t_high_cnt,clk->t_low_cnt);
		cfg.dword[1]=MSM_GPI_I2C_CONFIG0_TRE_DWORD1(0,0);
		cfg.dword[2]=MSM_GPI_I2C_CONFIG0_TRE_DWORD2(0,clk->clk_div);
		cfg.dword[3]=MSM_GPI_I2C_CONFIG0_TRE_DWORD3(0,0,0,0,1);
		go.dword[0]=MSM_GPI_I2C_GO_TRE_DWORD0(
			    a52ac_stretch,msg->addr,a52ac_op);
		go.dword[1]=MSM_GPI_I2C_GO_TRE_DWORD1;
		go.dword[2]=MSM_GPI_I2C_GO_TRE_DWORD2(rdmsg ? msg->len : 0);
		go.dword[3]=rdmsg ?
			MSM_GPI_I2C_GO_TRE_DWORD3(1,0,0,1,0) :
			MSM_GPI_I2C_GO_TRE_DWORD3(0,0,0,0,1);
		data.dword[0]=MSM_GPI_DMA_W_BUFFER_TRE_DWORD0(phys);
		data.dword[1]=MSM_GPI_DMA_W_BUFFER_TRE_DWORD1(phys);
		data.dword[2]=MSM_GPI_DMA_W_BUFFER_TRE_DWORD2(msg->len);
		data.dword[3]=MSM_GPI_DMA_W_BUFFER_TRE_DWORD3(0,0,1,0,0);
		sg_init_table(txsg,3);
		sg_set_buf(&txsg[n++],&cfg,sizeof(cfg));
		sg_set_buf(&txsg[n++],&go,sizeof(go));
		if (rdmsg) {
			sg_init_table(&rxsg,1);
			sg_set_buf(&rxsg,&data,sizeof(data));
			rd=dmaengine_prep_slave_sg(gi2c->a52ac_rx,&rxsg,1,
					DMA_DEV_TO_MEM,DMA_PREP_INTERRUPT|DMA_CTRL_ACK);
			if (!rd) {ret=-ENOMEM;goto cleanup;}
			rd->callback=a52ac_gsi_rx_cb;
			rd->callback_param=gi2c;
			ret=dma_submit_error(dmaengine_submit(rd));
			if (ret) goto cleanup;
			dma_async_issue_pending(gi2c->a52ac_rx);
		} else {
			sg_set_buf(&txsg[n++],&data,sizeof(data));
		}
		td=dmaengine_prep_slave_sg(gi2c->a52ac_tx,txsg,n,
				DMA_MEM_TO_DEV,DMA_PREP_INTERRUPT|DMA_CTRL_ACK);
		if (!td) {ret=-ENOMEM;goto cleanup;}
		if (!rdmsg) {
			td->callback=a52ac_gsi_tx_cb;
			td->callback_param=gi2c;
		}
		ret=dma_submit_error(dmaengine_submit(td));
		if (ret)goto cleanup;
		dev_info_ratelimited(dev,"P446AC GSI XFER_START msg=%d/%d addr=%#x dir=%c len=%u segs=%d\n",
			i+1,num,msg->addr,rdmsg?'R':'W',msg->len,n);
		dma_async_issue_pending(gi2c->a52ac_tx);
		timeout=wait_for_completion_timeout(&gi2c->done,msecs_to_jiffies(1100));
		if (!timeout) ret=-ETIMEDOUT;
		else if (gi2c->err)ret=gi2c->err;
cleanup:
		if (ret) {
			dmaengine_terminate_all(gi2c->a52ac_tx);
			dmaengine_terminate_all(gi2c->a52ac_rx);
		}
		dma_unmap_single(mapdev,phys,msg->len,
			rdmsg ? DMA_FROM_DEVICE : DMA_TO_DEVICE);
		i2c_put_dma_safe_msg_buf(buf,msg,!ret);
		dev_info_ratelimited(dev,"P446AC GSI XFER_DONE msg=%d/%d rc=%d\n",i+1,num,ret);
		if (ret)break;
	}
	return ret;
}
'''
def patch(s):
    if TAG in s:return s
    if "P446AB SE8_CAP" not in s or "P446Z I2C BUS_VOTE" not in s:
        raise RuntimeError("P446AC GSI needs 446AB source")
    s="#include <linux/types.h>\n#include <linux/dmaengine.h>\n#include <linux/msm_gpi.h>\n#include <linux/scatterlist.h>\n"+s
    a="struct geni_i2c_dev {"
    b=a+"""
	bool a52ac_gsi;
	struct dma_chan *a52ac_tx, *a52ac_rx;
	struct msm_gpi_ctrl a52ac_tx_ev, a52ac_rx_ev;
"""
    s=one(s,a,b,"GENI device GSI fields")
    s=one(s,"static int geni_i2c_xfer(struct i2c_adapter *adap,",
          GSI+"\nstatic int geni_i2c_xfer(struct i2c_adapter *adap,",
          "add GSI transport before FIFO xfer")
    s=one(s,"\tqcom_geni_i2c_conf(gi2c);\n\tfor (i = 0; i < num; i++) {",
          "\tif (gi2c->a52ac_gsi) {\n"
          "\t\tret=a52ac_gsi_xfer(gi2c,msgs,num);\n"
          "\t\tgoto a52ac_xfer_done;\n"
          "\t}\n"
          "\tqcom_geni_i2c_conf(gi2c);\n\tfor (i = 0; i < num; i++) {",
          "route GSI before any FIFO command")
    s=one(s,"\tif (ret == 0)\n\t\tret = num;\n\n\tpm_runtime_mark_last_busy",
          "\ta52ac_xfer_done:\n\tif (ret == 0)\n\t\tret = num;\n\n\tpm_runtime_mark_last_busy",
          "GSI common PM exit")
    s=one(s,"\tproto = geni_se_read_proto(&gi2c->se);",
          "\tproto = geni_se_read_proto(&gi2c->se);\n"
          "\tgi2c->a52ac_gsi=gi2c->se.a52_legacy_ahb &&\n"
          "\t\t!!readl_relaxed(gi2c->se.base+0x64);\n"
          "\tif (gi2c->a52ac_gsi)\n"
          "\t\tdev_info(dev,\"P446AC GSI_ONLY SE8 fifo_disabled=1 transport=TG_GPI\\n\");",
          "detect locked GSI firmware at probe")
    # ACK's FIFO initialization is wrong on firmware that locks the FIFO off.
    a="\tgi2c->tx_wm = tx_depth - 1;\n\tgeni_se_init(&gi2c->se, gi2c->tx_wm, tx_depth);"
    s=one(s,a,"\tif (!gi2c->a52ac_gsi) {\n"+a+"\n\t}", "gate FIFO hardware initialization")
    a="\tgeni_se_config_packing(&gi2c->se, BITS_PER_BYTE, PACKING_BYTES_PW,"
    s=one(s,a,"\tif (!gi2c->a52ac_gsi)\n"+a,"gate FIFO packing")
    s=one(s,"\tdev_dbg(dev, \"Geni-I2C adaptor successfully added\\n\");",
          "\tif (gi2c->a52ac_gsi) {\n"
          "\t\tret=devm_add_action_or_reset(dev,a52ac_gsi_release,gi2c);\n"
          "\t\tif (ret)return ret;\n"
          "\t}\n"
          "\tdev_dbg(dev, \"Geni-I2C adaptor successfully added\\n\");",
          "release GPI channels on driver teardown")
    s=one(s,"\tdisable_irq(gi2c->irq);\n\tret = geni_se_resources_off(&gi2c->se);",
          "\tif (!gi2c->a52ac_gsi)\n\t\tdisable_irq(gi2c->irq);\n"
          "\tret = geni_se_resources_off(&gi2c->se);",
          "no GSI slave IRQ disable")
    s=one(s,"\tenable_irq(gi2c->irq);\n\tgi2c->suspended = 0;",
          "\tif (!gi2c->a52ac_gsi)\n\t\tenable_irq(gi2c->irq);\n"
          "\tgi2c->suspended = 0;",
          "no FIFO IRQ enable for GSI")
    return s

def check(root):
    s=(root/PATH).read_text()
    for x in (TAG,"P446AC GSI_ONLY","P446AC GSI XFER_START",
              "a52ac_gsi_xfer(gi2c,msgs,num)","dmaengine_prep_slave_sg",
              "a52ac_gsi_release"):
        if x not in s:raise RuntimeError("P446AC GSI missing "+x)
    print("P446AC GSI contract PASS: SE8 gated GSI TRE transfer, FIFO other engines retained, PM remains balanced")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/PATH
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
