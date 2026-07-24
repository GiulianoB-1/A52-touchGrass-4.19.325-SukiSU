#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from pathlib import Path

ROOTS=('drivers/a52_display','techpack/display')
def r(p): return p.read_text(errors='replace')
def w(p,s): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
def disp(g,rel):
 o=[g/x/rel for x in ROOTS if (g/x/rel).is_file()]
 if not o: raise SystemExit('missing display source '+rel)
 return o
def sub(p,a,b,n=1):
 s=r(p);c=s.count(a)
 if c: w(p,s.replace(a,b,n));return min(c,n)
 return 0
def rx(p,a,b,n=1):
 s=r(p);s,c=re.subn(a,b,s,count=n,flags=re.M|re.S)
 if c:w(p,s)
 return c
def before_endif(p,mark,block):
 s=r(p)
 if mark in s:return 0
 i=s.rfind('#endif')
 if i<0:raise SystemExit('no endif '+str(p))
 w(p,s[:i]+'\n'+block.rstrip()+'\n\n'+s[i:]);return 1

def common(g):
 p=g/'a52-port-compat.h';s=r(p);m='A52_PHASE13_ALL_KNOWN_COMPAT_INCLUDES';c=0
 if m not in s:
  i=s.rfind('#endif');s=s[:i]+'''/* A52_PHASE13_ALL_KNOWN_COMPAT_INCLUDES */
#include <linux/mm.h>
#include <linux/io.h>
#include <linux/notifier.h>
#include <linux/dma-buf.h>
#include <linux/dma-map-ops.h>
#include <linux/mutex.h>
#include <linux/coresight.h>
#include <drm/drm_atomic.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_atomic_uapi.h>
#include <drm/drm_bridge.h>
#include <drm/drm_connector.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_plane_helper.h>
#include <drm/drm_probe_helper.h>
#include <drm/drm_prime.h>
'''+s[i:];w(p,s);c=1
 block=r'''/* A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS: diagnostic, non-flashable. */
#ifndef DRIVER_PRIME
#define DRIVER_PRIME 0
#endif
#ifndef CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR
#define CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR "msm-adreno-tz"
#endif
#ifndef devm_ioremap_nocache
#define devm_ioremap_nocache(d,o,z) devm_ioremap((d),(o),(z))
#endif
#ifndef DRM_DEBUG_DRIVER_RATELIMITED
#define DRM_DEBUG_DRIVER_RATELIMITED(f,...) DRM_DEBUG_DRIVER(f,##__VA_ARGS__)
#endif
#ifndef __drm_atomic_helper_disable_plane
#define __drm_atomic_helper_disable_plane(p,c) drm_atomic_helper_disable_plane((p),(c))
#endif
#ifndef __drm_atomic_helper_set_config
#define __drm_atomic_helper_set_config(s,c) drm_atomic_helper_set_config((s),(c))
#endif
#ifndef drm_plane_helper_disable
#define drm_plane_helper_disable(p,c) drm_atomic_helper_disable_plane((p),(c))
#endif
#ifndef drm_bridge_disable
#define drm_bridge_disable(b) drm_bridge_chain_disable((b))
#define drm_bridge_post_disable(b) drm_bridge_chain_post_disable((b))
#define drm_bridge_mode_set(b,m,a) drm_bridge_chain_mode_set((b),(m),(a))
#define drm_bridge_pre_enable(b) drm_bridge_chain_pre_enable((b))
#define drm_bridge_enable(b) drm_bridge_chain_enable((b))
#endif
#ifndef drm_mode_create_colorspace_property
#define drm_mode_create_colorspace_property(c) drm_mode_create_dp_colorspace_property((c))
#endif
static inline int a52_cpp(u32 f,int p){const struct drm_format_info*i=drm_format_info(f);return(!i||p<0||p>=i->num_planes)?0:i->cpp[p];}
#ifndef drm_format_plane_cpp
#define drm_format_plane_cpp(f,p) a52_cpp((f),(p))
#endif
static inline void*a52_kmap(struct dma_buf*b,unsigned long n){void*v=dma_buf_vmap(b);return v?(char*)v+n*PAGE_SIZE:NULL;}
static inline void a52_kunmap(struct dma_buf*b,unsigned long n,void*v){if(v)dma_buf_vunmap(b,(char*)v-n*PAGE_SIZE);}
#ifndef dma_buf_kmap
#define dma_buf_kmap(b,n) a52_kmap((b),(n))
#define dma_buf_kunmap(b,n,v) a52_kunmap((b),(n),(v))
#endif
#ifndef dma_release_declared_memory
#define dma_release_declared_memory(d) dma_release_coherent_memory((d))
#endif
static inline struct task_struct*a52_owner(struct mutex*l){return(struct task_struct*)((unsigned long)atomic_long_read(&l->owner)&~0x7UL);}
#ifndef __mutex_owner
#define __mutex_owner(l) a52_owner((l))
#endif
#ifndef of_get_coresight_platform_data
#define of_get_coresight_platform_data(d,n) coresight_get_platform_data((d))
#endif
static inline int a52_nb(struct notifier_block*n){return 0;}
static inline int a52_reclaim(struct address_space*m,void*d){return NOTIFY_DONE;}
#define show_mem_extra_notifier_register(n) a52_nb((n))
#define show_mem_extra_notifier_unregister(n) a52_nb((n))
#define proc_reclaim_notifier_register(n) a52_nb((n))
#define proc_reclaim_notifier_unregister(n) a52_nb((n))
#define reclaim_address_space(m,d) a52_reclaim((m),(d))
#define sched_set_refresh_rate(r) do{}while(0)
#define rpmh_mode_solver_set(d,e) do{}while(0)
#define rpmh_flush(d) do{}while(0)
'''
 return {'includes':c,'shims':before_endif(p,'A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS',block)}

def headers(g):
 out={};p=g/'include/linux/iommu.h';s=r(p)
 defs=[('IOMMU_FAULT_TRANSLATION','(1 << 2)'),('IOMMU_FAULT_PERMISSION','(1 << 3)'),('IOMMU_FAULT_EXTERNAL','(1 << 4)'),('IOMMU_FAULT_TRANSACTION_STALLED','(1 << 5)')]
 miss=[x for x in defs if not re.search(r'^#define\s+'+x[0]+r'\b',s,re.M)]
 if miss:
  q=re.search(r'^#define\s+IOMMU_FAULT_WRITE[^\n]*\n',s,re.M);assert q
  s=s[:q.end()]+'\n'.join('#define %s\t%s'%x for x in miss)+'\n'+s[q.end():]
 attrs=['DOMAIN_ATTR_CONTEXT_BANK','DOMAIN_ATTR_PROCID','DOMAIN_ATTR_TTBR0','DOMAIN_ATTR_CONTEXTIDR','DOMAIN_ATTR_SECURE_VMID','DOMAIN_ATTR_DYNAMIC','DOMAIN_ATTR_EARLY_MAP','DOMAIN_ATTR_FAULT_MODEL_NO_STALL','DOMAIN_ATTR_USE_LLC_NWA','DOMAIN_ATTR_USE_UPSTREAM_HINT']
 ma=[x for x in attrs if x not in s]
 if ma:s=s.replace('\tDOMAIN_ATTR_MAX,','\t/* A52 vendor attrs: provider semantics pending. */\n'+'\n'.join('\t'+x+',' for x in ma)+'\n\tDOMAIN_ATTR_MAX,',1)
 w(p,s);out['iommu']=len(miss)+len(ma)
 p=g/'include/linux/dma-mapping.h';s=r(p);ds=[('DMA_ATTR_STRONGLY_ORDERED','(1UL << 20)'),('DMA_ATTR_DELAYED_UNMAP','(1UL << 21)'),('DMA_ATTR_IOMMU_USE_UPSTREAM_HINT','(1UL << 22)'),('DMA_ATTR_IOMMU_USE_LLC_NWA','DMA_ATTR_SYS_CACHE_ONLY_NWA')];md=[x for x in ds if not re.search(r'^#define\s+'+x[0]+r'\b',s,re.M)]
 if md:s=s.replace('#define DMA_MAPPING_ERROR','/* A52 non-conflicting diagnostic attrs. */\n'+'\n'.join('#define %s\t%s'%x for x in md)+'\n#define DMA_MAPPING_ERROR',1);w(p,s)
 out['dma_attrs']=len(md)
 p=g/'include/drm/drm_dp_helper.h';s=r(p);cc=0
 if 'struct drm_dp_link {' not in s:
  i=s.rfind('#endif');s=s[:i]+'''struct drm_dp_link {
\tunsigned char revision;
\tunsigned int rate;
\tunsigned int num_lanes;
\tunsigned long capabilities;
};
'''+s[i:];cc+=1
 dp=[('DP_TEST_PHY_PATTERN_NONE','0x0'),('DP_TEST_PHY_PATTERN_D10_2_NO_SCRAMBLING','0x1'),('DP_TEST_PHY_PATTERN_SYMBOL_ERR_MEASUREMENT_CNT','0x2'),('DP_TEST_PHY_PATTERN_PRBS7','0x3'),('DP_TEST_PHY_PATTERN_80_BIT_CUSTOM_PATTERN','0x4'),('DP_TEST_PHY_PATTERN_CP2520_PATTERN_1','0x5'),('DP_TEST_PHY_PATTERN_CP2520_PATTERN_2','0x6'),('DP_TEST_PHY_PATTERN_CP2520_PATTERN_3','0x7')];mm=[x for x in dp if not re.search(r'^#\s*define\s+'+x[0]+r'\b',s,re.M)]
 if mm:
  q=re.search(r'^#define\s+DP_TEST_PHY_PATTERN[^\n]*\n',s,re.M);i=q.end() if q else s.rfind('#endif');s=s[:i]+'\n'.join('#define %s\t%s'%x for x in mm)+'\n'+s[i:];cc+=len(mm)
 w(p,s);out['dp']=cc
 p=g/'a52-compat/include/linux/ion_kernel.h';w(p,r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __LINUX_ION_KERNEL_H__
#define __LINUX_ION_KERNEL_H__
#include <linux/dma-buf.h>
#include <linux/err.h>
/* A52_PHASE13_ION_DIAGNOSTIC_STUBS */
static inline struct dma_buf*ion_alloc(size_t l,unsigned int h,unsigned int f){return ERR_PTR(-ENODEV);}
static inline unsigned int ion_get_flags_num_vm_elems(unsigned int f){return 0;}
static inline int ion_populate_vm_list(unsigned long f,unsigned int*v,int n){return -ENODEV;}
#endif
''');out['ion']=1;return out

def kgsl(g):
 o={};p=g/'drivers/gpu/msm/kgsl.c';o['fault']=rx(p,r'static\s+int\s+(kgsl[^\(\n]*fault\s*\(\s*struct\s+vm_fault\s*\*)',r'static vm_fault_t \1');o['qos']=sub(p,'struct pm_qos_request qos;','struct pm_qos_request qos __maybe_unused;')
 p=g/'drivers/gpu/msm/kgsl_pool.c';o['ram']=rx(p,r'\btotalram_pages\s*([<>]=?)',r'totalram_pages() \1')
 p=g/'drivers/gpu/msm/adreno_coresight.c';o['coresight']=rx(p,r'of_get_coresight_platform_data\s*\(\s*&pdev->dev\s*,\s*pdev->dev\.of_node\s*\)',r'coresight_get_platform_data(&pdev->dev)')
 p=g/'drivers/gpu/msm/kgsl_gmu.c';s=r(p);a='''\tunsigned int len;

\tmemset(arc, 0, sizeof(*arc));

\tlen = cmd_db_read_aux_data_len(res_id);
\tif (len == 0)
\t\treturn -EINVAL;
''';b='''\tsize_t len = 0;
\tconst void *aux;

\tmemset(arc, 0, sizeof(*arc));

\taux = cmd_db_read_aux_data(res_id, &len);
\tif (IS_ERR(aux))
\t\treturn PTR_ERR(aux);
\tif (len == 0)
\t\treturn -EINVAL;
''';n=0
 if a in s:s=s.replace(a,b,1);n+=1
 if '\tcmd_db_read_aux_data(res_id, (uint8_t *)arc->val, len);' in s:s=s.replace('\tcmd_db_read_aux_data(res_id, (uint8_t *)arc->val, len);','\tmemcpy(arc->val, aux, len);',1);n+=1
 if n:w(p,s)
 o['cmd_db']=n;return o

def one_display(p,rel):
 s=r(p);n=0
 def S(a,b):
  nonlocal s,n
  if a in s:s=s.replace(a,b,1);n+=1
 if rel=='msm/sde/sde_trace.h':S('\t\t\t__field(u32, bus_id);','\t\t\t__field(u32, bus_id)')
 elif rel=='msm/sde/sde_kms.c':S('smmu_state->sui_misr_state == NONE;','smmu_state->sui_misr_state = NONE;');S('if (sde_kms_power_resource_is_enabled(sde_kms->dev) < 0) {','if (!sde_kms_power_resource_is_enabled(sde_kms->dev)) {')
 elif rel=='msm/sde/sde_connector.c':
  a='''static int sde_connector_atomic_check(struct drm_connector *connector,
\t\tstruct drm_connector_state *new_conn_state)
{
\tstruct sde_connector *c_conn;
''';b='''static int sde_connector_atomic_check(struct drm_connector *connector,
\t\tstruct drm_atomic_state *state)
{
\tstruct sde_connector *c_conn;
\tstruct drm_connector_state *new_conn_state;
''';S(a,b)
  a='''\tif (!connector) {
\t\tSDE_ERROR("invalid connector\\n");
\t\treturn -EINVAL;
\t}

\tif (!new_conn_state) {''';b='''\tif (!connector) {
\t\tSDE_ERROR("invalid connector\\n");
\t\treturn -EINVAL;
\t}

\tnew_conn_state = drm_atomic_get_new_connector_state(state, connector);
\tif (!new_conn_state) {''';S(a,b)
 elif rel=='msm/dsi/dsi_drm.c':
  S('static int dsi_bridge_attach(struct drm_bridge *bridge)','static int dsi_bridge_attach(struct drm_bridge *bridge,\n\t\tenum drm_bridge_attach_flags flags)');S('{\n\tstruct dsi_bridge *c_bridge = to_dsi_bridge(bridge);','{\n\tstruct dsi_bridge *c_bridge = to_dsi_bridge(bridge);\n\n\t(void)flags;');S('struct drm_display_mode *mode,\n\t\t\t\tstruct drm_display_mode *adjusted_mode)','const struct drm_display_mode *mode,\n\t\t\t\tconst struct drm_display_mode *adjusted_mode)');S('drm_bridge_attach(encoder, &bridge->base, NULL)','drm_bridge_attach(encoder, &bridge->base, NULL, 0)')
 elif rel=='msm/dsi/dsi_display.c':
  S('size_t len;','size_t len = 0;');S('display->ext_conn, c_state);','display->ext_conn, c_state->state);');S('struct drm_bridge *bridge,\n\t\tconst struct drm_display_mode *mode)','struct drm_bridge *bridge,\n\t\tconst struct drm_display_info *info,\n\t\tconst struct drm_display_mode *mode)');S('mode_valid(bridge, &tmp)','mode_valid(bridge, info, &tmp)');S('struct drm_display_mode *mode,\n\t\tstruct drm_display_mode *adjusted_mode)','const struct drm_display_mode *mode,\n\t\tconst struct drm_display_mode *adjusted_mode)');S('drm_bridge_attach(encoder, ext_bridge, prev_bridge)','drm_bridge_attach(encoder, ext_bridge, prev_bridge, 0)')
 elif rel=='msm/dsi/dsi_panel.c':
  S('''    if (vdd->dtsi_data.samsung_dsi_off_reset_delay)
        usleep_range(vdd->dtsi_data.samsung_dsi_off_reset_delay,
                vdd->dtsi_data.samsung_dsi_off_reset_delay);''','''\tif (vdd->dtsi_data.samsung_dsi_off_reset_delay) {
\t\tusleep_range(vdd->dtsi_data.samsung_dsi_off_reset_delay,
\t\t\t\tvdd->dtsi_data.samsung_dsi_off_reset_delay);
\t}''')
  S('''\tdrm_panel_init(&panel->drm_panel);
\tpanel->drm_panel.dev = &panel->mipi_device.dev;''','''\tdrm_panel_init(&panel->drm_panel, &panel->mipi_device.dev,
\t\t\tNULL, DRM_MODE_CONNECTOR_DSI);''');S('''\trc = drm_panel_add(&panel->drm_panel);
\tif (rc)
\t\tgoto error;''','''\tdrm_panel_add(&panel->drm_panel);''')
 elif rel=='rotator/sde_rotator_util.c':
  S('static void *sde_rot_dmabuf_no_map(struct dma_buf *buf, unsigned long n)','static void *sde_rot_dmabuf_no_vmap(struct dma_buf *buf)');S('static void sde_rot_dmabuf_no_unmap(struct dma_buf *buf, unsigned long n,\n\t\tvoid *addr)','static void sde_rot_dmabuf_no_vunmap(struct dma_buf *buf, void *addr)');S('\t.map\t\t= sde_rot_dmabuf_no_map,','\t.vmap\t\t= sde_rot_dmabuf_no_vmap,');S('\t.unmap\t\t= sde_rot_dmabuf_no_unmap,','\t.vunmap\t\t= sde_rot_dmabuf_no_vunmap,')
 elif rel=='rotator/sde_rotator_dev.c':
  for x in ('\t.vidioc_cropcap           = sde_rotator_cropcap,\n','\t.vidioc_g_crop            = sde_rotator_g_crop,\n','\t.vidioc_s_crop            = sde_rotator_s_crop,\n'):
   if x in s:s=s.replace(x,'',1);n+=1
 elif rel in ('msm/msm_gem.c','msm/msm_gem_prime.c'):
  s,c=re.subn(r'drm_prime_pages_to_sg\(\s*p\s*,\s*npages\s*\)',r'drm_prime_pages_to_sg(dev, p, npages)',s);n+=c;s,c=re.subn(r'drm_prime_pages_to_sg\(\s*pages\s*,\s*nr_pages\s*\)',r'drm_prime_pages_to_sg(obj->dev, pages, nr_pages)',s);n+=c
 elif rel=='msm/sde/sde_hw_reg_dma_v1_color_proc.c':S('if (hw_cfg->dspp == NULL)','if (hw_cfg->dspp[0] == NULL)')
 elif rel=='msm/sde/sde_hw_reg_dma_v1.c':S('if (cfg->ctl->idx < CTL_0 && cfg->ctl->idx >= CTL_MAX)','if (cfg->ctl->idx < CTL_0 || cfg->ctl->idx >= CTL_MAX)')
 elif rel=='msm/sde/sde_hw_catalog.c':S('struct sde_vbif_cfg *vbif;','struct sde_vbif_cfg *vbif = NULL;')
 elif rel=='msm/sde/sde_rm.c':S('struct sde_connector *conn;','struct sde_connector *conn = NULL;')
 elif rel=='msm/msm_drv.c':S('int ret;','int ret = 0;')
 elif rel=='msm/sde/sde_crtc.c':
  S('struct sde_hw_ds *hw_ds;\n\tstruct sde_hw_ds_cfg *cfg;','struct sde_hw_ds *hw_ds = NULL;\n\tstruct sde_hw_ds_cfg *cfg = NULL;');S('\tcstate = to_sde_crtc_state(state);\n','\tcstate = to_sde_crtc_state(state);\n\tcfg = &cstate->ds_cfg[0];\n');s,c=re.subn(r'struct drm_plane \*plane;\n','struct drm_plane *plane = NULL;\n',s,count=1);n+=c
 if n:w(p,s)
 return n

def display(g):
 rels=['msm/sde/sde_trace.h','msm/sde/sde_kms.c','msm/sde/sde_connector.c','msm/dsi/dsi_drm.c','msm/dsi/dsi_display.c','msm/dsi/dsi_panel.c','rotator/sde_rotator_util.c','rotator/sde_rotator_dev.c','msm/msm_gem.c','msm/msm_gem_prime.c','msm/sde/sde_hw_reg_dma_v1_color_proc.c','msm/sde/sde_hw_reg_dma_v1.c','msm/sde/sde_hw_catalog.c','msm/sde/sde_rm.c','msm/msm_drv.c','msm/sde/sde_crtc.c'];o={}
 for x in rels:
  for p in disp(g,x):o[str(p.relative_to(g))]=one_display(p,x)
 return o

def validate(g):
 files=[g/'a52-port-compat.h',g/'include/linux/iommu.h',g/'include/linux/dma-mapping.h',g/'include/drm/drm_dp_helper.h',g/'a52-compat/include/linux/ion_kernel.h',g/'drivers/a52_display/msm/dsi/dsi_drm.c',g/'drivers/a52_display/msm/dsi/dsi_display.c',g/'drivers/a52_display/msm/sde/sde_connector.c',g/'drivers/a52_display/rotator/sde_rotator_util.c'];s=[r(x) for x in files]
 return {'compat':'A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS'in s[0],'iommu':'DOMAIN_ATTR_EARLY_MAP'in s[1],'dma':'DMA_ATTR_DELAYED_UNMAP'in s[2],'dp':'DP_TEST_PHY_PATTERN_CP2520_PATTERN_3'in s[3] and 'struct drm_dp_link {'in s[3],'ion':'A52_PHASE13_ION_DIAGNOSTIC_STUBS'in s[4],'dsi':'enum drm_bridge_attach_flags flags'in s[5] and 'NULL, 0)'in s[5],'ext':'prev_bridge, 0)'in s[6],'conn':'struct drm_atomic_state *state'in s[7],'vmap':'.vmap'in s[8] and '.vunmap'in s[8]}

def main():
 a=argparse.ArgumentParser();a.add_argument('--touchgrass',type=Path,required=True);a.add_argument('--gki',type=Path,required=True);a.add_argument('--output',type=Path,required=True);x=a.parse_args();g=x.gki.resolve();x.output.mkdir(parents=True,exist_ok=True);q={'status':'phase13-all-known-compat-staged','flashable':False,'hardware_validated':False,'scope':'all 139 post-Workflow-114 errors','common':common(g),'headers':headers(g),'kgsl':kgsl(g),'display':display(g),'fallbacks':['ION returns -ENODEV','memory/reclaim/scheduler/RPMh vendor hooks are no-ops','legacy crop ioctls omitted','IOMMU provider semantics pending']};q['validation']=validate(g);(x.output/'phase13-all-known-compat-report.json').write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');bad=[k for k,v in q['validation'].items() if not v];
 if bad:raise SystemExit('Workflow 115 validation failed: '+','.join(bad))
if __name__=='__main__':main()
