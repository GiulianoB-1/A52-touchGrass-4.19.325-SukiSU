#!/usr/bin/env python3
"""Phase446Z SE8 QUPv3_1 core/memory bus votes and FTS fail-fast.

Targets only the Phase446Y generated source. No USB, DRM, panel, GPU,
power key, serial console or firmware flashing changes. Fail closed.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE446Z_QUP_CORE1_TOUCH_FAILFAST_V1"
I2C=Path("drivers/i2c/busses/i2c-qcom-geni.c")
STM=Path("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c")

def one(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P446Z {name}: anchor count {n}")
    return s.replace(a,b,1)

def function(s,name):
    a=s.index(name)
    b=s.index("\n}\n",a)+3
    return s[:a],s[a:b],s[b:]

BUS=r'''
/* A52_PHASE446Z_QUP_CORE1_TOUCH_FAILFAST_V1
 * DT wrapper has only two (master,slave) pairs and no usecase table.
 * Synthesize the two-case table, using ONLY the four wrapper DT cells.
 * Values below are B/s for legacy MSM-BUS (ICC kB/s x 1000):
 * CORE_2X_19_2_MHZ=960 kB/s, GENI_DEFAULT_BW=1 kB/s.
 * Never touch the bus controller owned by TZ/ADSP.
 */
#define A52Z_CORE_AB_BPS 960000ULL
#define A52Z_CORE_IB_BPS 960000ULL
#define A52Z_MEM_AB_BPS 1000ULL
#define A52Z_MEM_IB_BPS 1000ULL

static int a52z_bus_vote(struct geni_i2c_dev *gi2c, bool active)
{
	int rc;
	if (!gi2c->a52z_bus_client)
		return -ENODEV;
	if (gi2c->a52z_bus_active == active)
		return 0;
	rc=msm_bus_scale_client_update_request(gi2c->a52z_bus_client,
						active ? 1 : 0);
	dev_info(gi2c->se.dev,
		"P446Z I2C BUS_VOTE client=%u active=%u case=%u core_ab=%llu core_ib=%llu mem_ab=%llu mem_ib=%llu rc=%d\n",
		gi2c->a52z_bus_client,active,active ? 1 : 0,
		(unsigned long long)(active ? A52Z_CORE_AB_BPS : 0),
		(unsigned long long)(active ? A52Z_CORE_IB_BPS : 0),
		(unsigned long long)(active ? A52Z_MEM_AB_BPS : 0),
		(unsigned long long)(active ? A52Z_MEM_IB_BPS : 0),rc);
	if (!rc)
		gi2c->a52z_bus_active=active;
	return rc;
}

static void a52z_bus_cleanup(void *arg)
{
	struct geni_i2c_dev *gi2c=arg;
	if (!gi2c->a52z_bus_client)
		return;
	if (gi2c->a52z_bus_active)
		a52z_bus_vote(gi2c,false);
	msm_bus_scale_unregister_client(gi2c->a52z_bus_client);
	dev_info(gi2c->se.dev,"P446Z I2C BUS_UNREGISTER client=%u\n",
		gi2c->a52z_bus_client);
	gi2c->a52z_bus_client=0;
	gi2c->a52z_bus_active=false;
}

static int a52z_bus_register(struct geni_i2c_dev *gi2c,
			      struct platform_device *pdev)
{
	struct device *dev=&pdev->dev;
	struct device_node *node;
	struct resource res;
	u32 ids[4],paths;
	int rc,i;
	node=of_parse_phandle(dev->of_node,"qcom,wrapper-core",0);
	if (!node)
		return dev_err_probe(dev,-EINVAL,"P446Z I2C missing wrapper phandle\n");
	rc=of_address_to_resource(node,0,&res);
	if (rc || res.start!=0x009c0000 ||
	    !of_device_is_compatible(node,"qcom,qupv3-geni-se")) {
		dev_err(dev,"P446Z I2C unsupported wrapper rc=%d base=%pa (SE8 only)\n",
			rc,rc ? NULL : &res.start);
		of_node_put(node);
		return -ENODEV;
	}
	rc=of_property_read_u32(node,"qcom,msm-bus,num-paths",&paths);
	if (rc || paths!=2 ||
	    of_property_count_u32_elems(node,"qcom,msm-bus,vectors-bus-ids")!=4) {
		dev_err(dev,"P446Z I2C wrapper bus schema mismatch rc=%d paths=%u\n",
			rc,rc ? 0 : paths);
		of_node_put(node);
		return -EINVAL;
	}
	rc=of_property_read_u32_array(node,"qcom,msm-bus,vectors-bus-ids",ids,4);
	of_node_put(node);
	if (rc || !ids[0] || !ids[1] || !ids[2] || !ids[3] ||
	    (ids[0]==ids[2] && ids[1]==ids[3]))
		return dev_err_probe(dev,-EINVAL,"P446Z I2C wrapper invalid bus IDs rc=%d\n",rc);
	for(i=0;i<2;i++) {
		gi2c->a52z_paths[i].num_paths=2;
		gi2c->a52z_paths[i].vectors=gi2c->a52z_vec[i];
		gi2c->a52z_vec[i][0].src=ids[0];
		gi2c->a52z_vec[i][0].dst=ids[1];
		gi2c->a52z_vec[i][1].src=ids[2];
		gi2c->a52z_vec[i][1].dst=ids[3];
	}
	gi2c->a52z_vec[1][0].ab=A52Z_CORE_AB_BPS;
	gi2c->a52z_vec[1][0].ib=A52Z_CORE_IB_BPS;
	gi2c->a52z_vec[1][1].ab=A52Z_MEM_AB_BPS;
	gi2c->a52z_vec[1][1].ib=A52Z_MEM_IB_BPS;
	gi2c->a52z_pdata.usecase=gi2c->a52z_paths;
	gi2c->a52z_pdata.num_usecases=2;
	gi2c->a52z_pdata.name="P446Z_SE8_QUP_CORE1";
	gi2c->a52z_pdata.active_only=0;
	dev_info(dev,"P446Z I2C BUS_PATHS wrapper=0x9c0000 core=%u->%u mem=%u->%u num_paths=2 cases=2\n",
		ids[0],ids[1],ids[2],ids[3]);
	gi2c->a52z_bus_client=msm_bus_scale_register_client(&gi2c->a52z_pdata);
	if (!gi2c->a52z_bus_client)
		return dev_err_probe(dev,-EPROBE_DEFER,"P446Z I2C msm_bus registration unavailable\n");
	rc=devm_add_action_or_reset(dev,a52z_bus_cleanup,gi2c);
	if (rc)
		return rc;
	rc=a52z_bus_vote(gi2c,true);
	dev_info(dev,"P446Z I2C BUS_REGISTER client=%u rc=%d first_transfer_gated=1\n",
		gi2c->a52z_bus_client,rc);
	return rc;
}

static void a52z_pinctrl_witness(struct geni_i2c_dev *gi2c,int rc)
{
	struct device_node *state,*child;
	const char *function="(not-described)";
	state=of_parse_phandle(gi2c->se.dev->of_node,"pinctrl-0",0);
	if (state) {
		if (of_property_read_string(state,"function",&function)) {
			child=of_get_next_available_child(state,NULL);
			if (child) {
				of_property_read_string(child,"function",&function);
				of_node_put(child);
			}
		}
	}
	dev_info(gi2c->se.dev,
		"P446Z I2C PINCTRL state=default rc=%d node=%pOF function=%s irq=%d irq_count=%d\n",
		rc,state,function,gi2c->irq,
		atomic_read(&gi2c->a52z_irq_count));
	of_node_put(state);
}
'''

def patch_i2c(s):
    if TAG in s:return s
    s=one(s,"#include <linux/of_platform.h> /* A52_PHASE446Y",
          "#include <linux/msm-bus.h>\n#include <linux/of_platform.h> /* A52_PHASE446Y",
          "include msm bus")
    s=one(s,"\tint suspended;\n",
          "\tint suspended;\n\t/* "+TAG+" */\n"
          "\tu32 a52z_bus_client;\n\tbool a52z_bus_active;\n"
          "\tstruct msm_bus_scale_pdata a52z_pdata;\n"
          "\tstruct msm_bus_paths a52z_paths[2];\n"
          "\tstruct msm_bus_vectors a52z_vec[2][2];\n"
          "\tatomic_t a52z_irq_count;\n", "state")
    s=one(s,"static int geni_i2c_probe(struct platform_device *pdev)",
          BUS+"\nstatic int geni_i2c_probe(struct platform_device *pdev)",
          "helpers")
    s=one(s,"    atomic_set(&gi2c->a52y_first_xfer,0);",
          "    atomic_set(&gi2c->a52y_first_xfer,0);\n"
          "    atomic_set(&gi2c->a52z_irq_count,0);","init IRQ")
    s=one(s,"        gi2c->se.a52_legacy_ahb = true;",
          "        gi2c->se.a52_legacy_ahb = true;\n"
          "        ret=a52z_bus_register(gi2c,pdev);\n"
          "        if (ret)\n"
          "            return ret;","first vote before probe transfers")
    s=one(s,"\tspin_lock(&gi2c->lock);\n\tm_stat = readl_relaxed(base + SE_GENI_M_IRQ_STATUS);",
          "\tatomic_inc(&gi2c->a52z_irq_count);\n"
          "\tspin_lock(&gi2c->lock);\n\tm_stat = readl_relaxed(base + SE_GENI_M_IRQ_STATUS);",
          "IRQ count")
    s=one(s,"\tgi2c->err = 0;\n\treinit_completion(&gi2c->done);",
          "\tif (gi2c->se.a52_legacy_ahb &&\n"
          "\t    !atomic_read(&gi2c->a52y_first_xfer))\n"
          "\t\tdev_info(gi2c->se.dev,\"P446Z I2C FIRST_BEFORE irq=%d count=%d client=%u vote=%d\\n\",\n"
          "\t\t\tgi2c->irq,atomic_read(&gi2c->a52z_irq_count),\n"
          "\t\t\tgi2c->a52z_bus_client,gi2c->a52z_bus_active);\n"
          "\tgi2c->err = 0;\n\treinit_completion(&gi2c->done);","first before")
    s=one(s,"\tif (ret == -ETIMEDOUT)\n\t\tdev_err(gi2c->se.dev,\"P446Y I2C TIMEOUT",
          "\tif (gi2c->se.a52_legacy_ahb &&\n"
          "\t    atomic_read(&gi2c->a52y_first_xfer)==1)\n"
          "\t\tdev_info_once(gi2c->se.dev,\"P446Z I2C FIRST_AFTER irq=%d count=%d irq_status=%#x geni_status=%#x ios=%#x rc=%d\\n\",\n"
          "\t\t\tgi2c->irq,atomic_read(&gi2c->a52z_irq_count),\n"
          "\t\t\treadl_relaxed(gi2c->se.base+SE_GENI_M_IRQ_STATUS),\n"
          "\t\t\treadl_relaxed(gi2c->se.base+SE_GENI_STATUS),\n"
          "\t\t\treadl_relaxed(gi2c->se.base+SE_GENI_IOS),ret);\n"
          "\tif (ret == -ETIMEDOUT)\n\t\tdev_err(gi2c->se.dev,\"P446Y I2C TIMEOUT",
          "first after")
    # Preserve existing 446Y timeout witness; don't silently imply ICC voted.
    s=one(s,"icc=absent-legacy-ahb-only\\n",
          "legacy_client=%u voted=%d irq_count=%d\\n","time out evidence") if False else s
    old='\tret = geni_icc_enable(&gi2c->se);\n\tif (ret)\n\t\treturn ret;'
    new=('\tif (gi2c->se.a52_legacy_ahb) {\n'
         '\t\tret=a52z_bus_vote(gi2c,true);\n'
         '\t\tif (ret)return ret;\n'
         '\t}\n'
         '\tret = geni_icc_enable(&gi2c->se);\n'
         '\tif (ret) {\n'
         '\t\tif (gi2c->se.a52_legacy_ahb)a52z_bus_vote(gi2c,false);\n'
         '\t\treturn ret;\n\t}')
    s=one(s,old,new,"runtime resume vote")
    s=one(s,"\tret = geni_se_resources_on(&gi2c->se);\n\tif (ret)\n\t\treturn ret;\n\n\tenable_irq(gi2c->irq);",
          "\tret = geni_se_resources_on(&gi2c->se);\n"
          "\tif (gi2c->se.a52_legacy_ahb)a52z_pinctrl_witness(gi2c,ret);\n"
          "\tif (ret) {\n"
          "\t\tif (gi2c->se.a52_legacy_ahb)a52z_bus_vote(gi2c,false);\n"
          "\t\treturn ret;\n\t}\n\n"
          "\tenable_irq(gi2c->irq);","resume pinctrl and failure rollback")
    s=one(s,"\treturn geni_icc_disable(&gi2c->se);",
          "\tret=geni_icc_disable(&gi2c->se);\n"
          "\tif (ret)return ret;\n"
          "\tif (gi2c->se.a52_legacy_ahb)\n"
          "\t\treturn a52z_bus_vote(gi2c,false);\n"
          "\treturn 0;","runtime suspend off")
    return s

def patch_touch(s):
    if TAG in s:return s
    s=one(s,'\t\tinfo->comm_err_count++;\n',
          '\t\tif (ret == -ETIMEDOUT && !info->probe_done) {\n'
          '\t\t\tpr_err_once("P446Z TOUCH FIRST_I2C_TIMEOUT rc=%d stop retries\\n",ret);\n'
          '\t\t\tbreak;\n\t\t}\n'
          '\t\tinfo->comm_err_count++;\n',"write retry stop") if False else s
    anchor='\t\t\tinfo->comm_err_count++;\n'
    if s.count(anchor)!=2:
        raise RuntimeError(f"P446Z STM retry anchors expected 2 found {s.count(anchor)}")
    s=s.replace(anchor,
        '\t\t\tif (ret == -ETIMEDOUT && !info->probe_done) {\n'
        '\t\t\t\tpr_err_once("P446Z TOUCH FIRST_I2C_TIMEOUT rc=%d stop retries\\n",ret);\n'
        '\t\t\t\tbreak;\n\t\t\t}\n'+anchor)
    s=one(s,'\trc = fts_read_reg(info, &regAdd[0], 1, (u8 *)data, FTS_EVENT_SIZE);\n'
          '\tif (rc == -ENOTCONN)',
          '\trc = fts_read_reg(info, &regAdd[0], 1, (u8 *)data, FTS_EVENT_SIZE);\n'
          '\tif (rc == -ETIMEDOUT) {\n'
          '\t\tpr_err("P446Z TOUCH INIT_ABORT first I2C timeout -110, no resets or firmware writes\\n");\n'
          '\t\treturn rc;\n\t}\n'
          '\tif (rc == -ENOTCONN)',"fts_init abort")
    s=one(s,'\t\t.name = FTS_TS_DRV_NAME,\n',
          '\t\t.name = FTS_TS_DRV_NAME,\n'
          '\t\t.probe_type = PROBE_PREFER_ASYNCHRONOUS, /* '+TAG+' */\n',"async driver probe")
    start=s.index("static int fts_power_ctrl(void *data, bool on)")
    end=s.index("static int fts_parse_dt(",start)
    part=s[start:end]
    part=one(part,"\tstatic bool enabled;\n\tstatic bool boot_on = true;",
          "\tstatic bool enabled;\n\tstatic bool avdd_held;\n\tstatic bool dvdd_held;\n"
          "\tstatic bool boot_on = true;","rail reference flags")
    part=one(part,"\t\t\tretval = regulator_enable(info->regulator_avdd);\n"
                  "\t\t\tif (retval) {",
          "\t\t\tretval = regulator_enable(info->regulator_avdd);\n"
          "\t\t\tif (!retval)avdd_held=true;\n"
          "\t\t\tif (retval) {","avdd acquired")
    part=one(part,"\t\t\tretval = regulator_enable(info->regulator_dvdd);\n"
                  "\t\t\tif (retval) {",
          "\t\t\tretval = regulator_enable(info->regulator_dvdd);\n"
          "\t\t\tif (!retval)dvdd_held=true;\n"
          "\t\t\tif (retval) {","dvdd acquired")
    part=one(part,"\t\t\t\tregulator_disable(info->regulator_avdd);\n\t\t\t\tgoto out;",
          "\t\t\t\tif (avdd_held) {\n"
          "\t\t\t\t\tif (!regulator_disable(info->regulator_avdd))avdd_held=false;\n"
          "\t\t\t\t}\n\t\t\t\tgoto out;","dvdd enable error unwind")
    part=one(part,"\t\tif (regulator_is_enabled(info->regulator_dvdd)) {",
          "\t\tif (dvdd_held) {","dvdd disable owned ref")
    part=one(part,"\t\t\tretval = regulator_disable(info->regulator_dvdd);\n"
                  "\t\t\tif (retval) {",
          "\t\t\tretval = regulator_disable(info->regulator_dvdd);\n"
          "\t\t\tif (!retval)dvdd_held=false;\n"
          "\t\t\tif (retval) {","dvdd disable state")
    part=one(part,"\t\tif (regulator_is_enabled(info->regulator_avdd)) {",
          "\t\tif (avdd_held) {","avdd disable owned ref")
    part=one(part,"\t\t\tretval = regulator_disable(info->regulator_avdd);\n"
                  "\t\t\tif (retval) {",
          "\t\t\tretval = regulator_disable(info->regulator_avdd);\n"
          "\t\t\tif (!retval)avdd_held=false;\n"
          "\t\t\tif (retval) {","avdd disable state")
    part=one(part,"\t\t\t\tregulator_enable(info->regulator_dvdd);\n"
                  "\t\t\t\tgoto out;",
          "\t\t\t\tif (!dvdd_held && !regulator_enable(info->regulator_dvdd))\n"
          "\t\t\t\t\tdvdd_held=true;\n"
          "\t\t\t\tgoto out;","avdd failure rollback")
    part=one(part,"\tenabled = on;\n",
          '\tpr_info("P446Z TOUCH RAIL_REFS avdd=%d dvdd=%d requested=%d rc=%d\\n",\n'
          '\t\tavdd_held,dvdd_held,on,retval);\n'
          "\tenabled = on;\n","rail witness")
    s=s[:start]+part+s[end:]
    return "/* "+TAG+" */\n"+s

def check(root):
    p=(root/I2C).read_text()
    for word in (TAG,"P446Z I2C BUS_PATHS","P446Z I2C BUS_REGISTER","P446Z I2C BUS_VOTE",
                 "qcom,msm-bus,vectors-bus-ids","msm_bus_scale_register_client",
                 "a52z_bus_vote(gi2c,true)","a52z_bus_vote(gi2c,false)",
                 "P446Z I2C FIRST_AFTER","P446Z I2C PINCTRL"):
        if word not in p:raise RuntimeError("P446Z missing I2C "+word)
    t=(root/STM).read_text()
    for word in (TAG,"PROBE_PREFER_ASYNCHRONOUS","P446Z TOUCH INIT_ABORT",
                 "P446Z TOUCH FIRST_I2C_TIMEOUT","dvdd_held","avdd_held",
):
        if word not in t:raise RuntimeError("P446Z missing STM "+word)
    fwu=(root/"drivers/input/touchscreen/stm/fts5cu56a/fts_fwu.c").read_text()
    if "FW_WRITE_BLOCKED" not in fwu or "A52_PHASE446X_STM_FTS_READ_ONLY_V1" not in fwu:
        raise RuntimeError("P446Z missing mandatory firmware-write safeguards in fts_fwu.c")
    print("P446Z contract PASS: wrapper DT pairs -> nonzero core/memory vote before SE8, resume/suspend vote, IRQ/pinctrl witnesses, async STM first timeout, owned regulator refs; firmware still read-only")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only:
        results=[]
        for path,fn in ((I2C,patch_i2c),(STM,patch_touch)):
            current=(a.root/path).read_text()
            results.append((a.root/path,fn(current)))
        for path,new in results:path.write_text(new)
    check(a.root)
if __name__=="__main__":main()
