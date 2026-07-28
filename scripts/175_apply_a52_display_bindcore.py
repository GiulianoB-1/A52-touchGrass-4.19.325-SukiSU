#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, tempfile
from pathlib import Path

OLD='heap19-bufops-display-lifecycle-v1'; PROFILE='heap19-bufops-display-bindcore-v1'
CAPTURE='ea858f328fd30d2ccb3a06e6bff0a52346e3df8e87672d935c96798d2fc613d1'
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MK=Path('drivers/a52_secure/Makefile')
AUD=Path('drivers/a52_secure/a52_display_bind_audit.c')
INC='#include <linux/a52_ack_secure_flight_recorder.h>'

PATCHES={
Path('drivers/a52_display/msm/msm_drv.c'):(
'''static int __init msm_drm_register(void)
{
\tA52_ACKFR_SCOPE("DISP", "a52.life.msm_drm_register");
\tif (!modeset)
\t\treturn -EINVAL;

\tDBG("init");
\tmsm_smmu_driver_init();
\tmsm_dsi_register();
\tmsm_edp_register();
\tmsm_hdmi_register();
\treturn platform_driver_register(&msm_platform_driver);
}
''',
'''static int __init msm_drm_register(void)
{
\tint rc;

\tA52_ACKFR_SCOPE("DISP", "a52.life.msm_drm_register");
\tif (!modeset) {
\t\ta52_ackfr_record("DISP bind reg=msm_drm rc=%d", -EINVAL);
\t\treturn -EINVAL;
\t}

\tDBG("init");
\tmsm_smmu_driver_init();
\tmsm_dsi_register();
\tmsm_edp_register();
\tmsm_hdmi_register();
\trc = platform_driver_register(&msm_platform_driver);
\ta52_ackfr_record("DISP bind reg=msm_drm rc=%d", rc);
\treturn rc;
}
'''),
Path('drivers/a52_display/msm/dsi/dsi_display.c'):(
'''static int __init dsi_display_register(void)
{
\tA52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_register");
\tdsi_phy_drv_register();
\tdsi_ctrl_drv_register();

\tdsi_display_parse_boot_display_selection();

\treturn platform_driver_register(&dsi_display_driver);
}
''',
'''static int __init dsi_display_register(void)
{
\tint rc;

\tA52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_register");
\tdsi_phy_drv_register();
\tdsi_ctrl_drv_register();
\tdsi_display_parse_boot_display_selection();
\trc = platform_driver_register(&dsi_display_driver);
\ta52_ackfr_record("DISP bind reg=dsi_display rc=%d", rc);
\treturn rc;
}
'''),
Path('drivers/a52_display/msm/dsi/dsi_phy.c'):(
'''void dsi_phy_drv_register(void)
{
\tplatform_driver_register(&dsi_phy_platform_driver);
}
''',
'''void dsi_phy_drv_register(void)
{
\tint rc = platform_driver_register(&dsi_phy_platform_driver);
\ta52_ackfr_record("DISP bind reg=dsi_phy rc=%d", rc);
}
'''),
Path('drivers/a52_display/msm/dsi/dsi_ctrl.c'):(
'''void dsi_ctrl_drv_register(void)
{
\tplatform_driver_register(&dsi_ctrl_driver);
}
''',
'''void dsi_ctrl_drv_register(void)
{
\tint rc = platform_driver_register(&dsi_ctrl_driver);
\ta52_ackfr_record("DISP bind reg=dsi_ctrl rc=%d", rc);
}
''')}

AUDIT='''// SPDX-License-Identifier: GPL-2.0-only
#include <linux/atomic.h>
#include <linux/device.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/jiffies.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/workqueue.h>
#include <linux/a52_ack_secure_flight_recorder.h>

/* driver_find(name, &platform_bus_type) is deliberately not used here. */
struct a52_bind_target { const char *tag; const char *compatible; };
static const struct a52_bind_target targets[] = {
\t{ "sde", "qcom,sde-kms" }, { "dsi", "qcom,dsi-display" },
\t{ "ctrl", "qcom,dsi-ctrl-hw-v2.4" }, { "phy", "qcom,dsi-phy-v3.0" },
};
static const char *bound_driver(const struct platform_device *p)
{
\treturn p && p->dev.driver && p->dev.driver->name ? p->dev.driver->name : "-";
}
static void audit_compat(const struct a52_bind_target *t, unsigned int pass)
{
\tstruct device_node *np = NULL; unsigned int n = 0;
\tfor_each_compatible_node(np, NULL, t->compatible) {
\t\tstruct platform_device *p = of_find_device_by_node(np);
\t\ta52_ackfr_record("DISP bind p=%u c=%s n=%u av=%u pdev=%u drv=%s",
\t\t\tpass, t->tag, n, of_device_is_available(np), !!p, bound_driver(p));
\t\tif (p) put_device(&p->dev);
\t\tn++;
\t}
\tif (!n) a52_ackfr_record("DISP bind p=%u c=%s nodes=0", pass, t->tag);
}
static void audit_all(unsigned int pass)
{
\tunsigned int i;
\tfor (i = 0; i < ARRAY_SIZE(targets); i++) audit_compat(&targets[i], pass);
}
static atomic_t pass_count = ATOMIC_INIT(0);
static void audit_workfn(struct work_struct *unused);
static DECLARE_DELAYED_WORK(audit_work, audit_workfn);
static void audit_workfn(struct work_struct *unused)
{
\tunsigned int pass = (unsigned int)atomic_inc_return(&pass_count);
\taudit_all(pass);
\tif (pass < 4) schedule_delayed_work(&audit_work,
\t\tmsecs_to_jiffies(pass == 1 ? 2000 : pass == 2 ? 8000 : 20000));
}
static int __init a52_display_bind_audit_init(void)
{
\ta52_ackfr_record("DISP bind audit=start"); audit_all(0);
\tschedule_delayed_work(&audit_work, msecs_to_jiffies(500)); return 0;
}
late_initcall(a52_display_bind_audit_init);
'''

def add_include(s:str)->str:
    if INC in s: return s
    off=0; pos=-1; seen=False
    for line in s.splitlines(keepends=True):
        if line.startswith('#include'): seen=True; pos=off+len(line)
        elif seen and line.strip() and not line.strip().startswith(('/*','*','//')): break
        off+=len(line)
    if pos < 0: raise SystemExit('include anchor missing')
    return s[:pos]+INC+'\n'+s[pos:]

def apply(root:Path,out:Path):
    rec=root/REC; mk=root/MK
    r=rec.read_text()
    if f'profile={PROFILE}' not in r:
        if r.count(f'profile={OLD}') != 1: raise SystemExit('profile anchor mismatch')
        rec.write_text(r.replace(f'profile={OLD}',f'profile={PROFILE}',1))
    changed={}
    for rel,(old,new) in PATCHES.items():
        p=root/rel; s=add_include(p.read_text())
        if new in s: changed[str(rel)]=False
        else:
            if s.count(old)!=1: raise SystemExit(f'anchor mismatch: {rel}')
            p.write_text(s.replace(old,new,1)); changed[str(rel)]=True
    a=root/AUD
    if a.exists() and a.read_text()!=AUDIT: raise SystemExit('unexpected audit source')
    if not a.exists(): a.write_text(AUDIT)
    marker='# A52_DISPLAY_BIND_AUDIT_V1\nobj-y += a52_display_bind_audit.o\n'
    m=mk.read_text()
    if marker not in m: mk.write_text(m+('' if m.endswith('\n') else '\n')+marker)
    report={
      'status':'a52-display-bindcore-v1-staged','hardware_validated':False,
      'functional_change':'instrumentation-only','persistent_profile':PROFILE,
      'capture_sha256':CAPTURE,'changed':changed,
      'observed_capture':{'qseecom_path_success':True,'kernel_alive_seconds_at_least':55,
        'display_registration_scopes_seen':['dsi_display_register','msm_drm_register'],
        'display_probe_or_bind_scopes_seen':[]},
      'instrumentation':{'registration_return_codes':['dsi_phy','dsi_ctrl','dsi_display','msm_drm'],
        'compatible_node_audit':['qcom,sde-kms','qcom,dsi-display','qcom,dsi-ctrl-hw-v2.4','qcom,dsi-phy-v3.0'],
        'creates_platform_devices':False,'forces_reprobe':False,'audit_passes':5},
      'unchanged':{'heap19_get_flags':True,'qseecom_control_flow':True,
        'display_probe_control_flow':True,'panel_commands':True,'device_tree':True}}
    out.mkdir(parents=True,exist_ok=True)
    (out/'phase33-a52-display-bindcore-report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report

def selftest():
    with tempfile.TemporaryDirectory() as t:
        r=Path(t); (r/REC).parent.mkdir(parents=True)
        (r/REC).write_text(f'profile={OLD}\n'); (r/MK).write_text('obj-y += x.o\n')
        for rel,(old,_) in PATCHES.items():
            p=r/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('#include <linux/kernel.h>\n'+old)
        apply(r,r/'o1'); apply(r,r/'o2')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--gki',type=Path); ap.add_argument('--output',type=Path); ap.add_argument('--self-test',action='store_true'); a=ap.parse_args()
    if a.self_test: selftest(); print('{"status":"self-test-passed"}'); return 0
    if not a.gki or not a.output: ap.error('--gki and --output required')
    print(json.dumps(apply(a.gki.resolve(),a.output.resolve()),indent=2,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
