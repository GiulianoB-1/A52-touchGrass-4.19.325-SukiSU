#!/usr/bin/env python3
from pathlib import Path
import sys

UPSTREAM_A1 = "700f3a7c7fa5764c9f24bbf7c78e0b6e479fa653"
BASELINE = "a8389945c3dc63ec401287b92293f33d6ffa3d65"


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def function_span(text, signature):
    """
    Return the real C function definition, not an earlier forward declaration.

    Several Samsung files declare static functions near the top of the file
    before defining them later. A blind find(signature) followed by find("{")
    can therefore jump from a prototype into an unrelated initializer.
    """
    search = 0
    while True:
        start = text.find(signature, search)
        if start < 0:
            raise SystemExit(f"missing function definition: {signature}")

        brace = text.find("{", start)
        semi = text.find(";", start)

        if brace >= 0 and (semi < 0 or brace < semi):
            break

        search = start + len(signature)

    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1

    raise SystemExit(f"unterminated function: {signature}")


def function_block(text, signature):
    a, b = function_span(text, signature)
    return text[a:b]


def replace_function(text, signature, replacement):
    a, b = function_span(text, signature)
    return text[:a] + replacement + text[b:]


def require(path):
    if not path.is_file():
        raise SystemExit(f"missing required file: {path}")
    return path.read_text()


def patch_a1(root):
    path = root / "fs/f2fs/file.c"
    s = require(path)
    funcs = (
        "f2fs_ioc_start_atomic_write",
        "f2fs_ioc_commit_atomic_write",
        "f2fs_ioc_start_volatile_write",
        "f2fs_ioc_release_volatile_write",
        "f2fs_ioc_abort_volatile_write",
    )
    owner = "\tif (!inode_owner_or_capable(inode))\n\t\treturn -EACCES;\n"
    guard = "\tif (!(filp->f_mode & FMODE_WRITE))\n\t\treturn -EBADF;\n\n"
    for name in funcs:
        sig = f"static int {name}(struct file *filp)"
        a, b = function_span(s, sig)
        block = s[a:b]
        count = block.count("if (!(filp->f_mode & FMODE_WRITE))")
        if count == 0:
            if block.count(owner) != 1:
                raise SystemExit(f"A1 {name}: ownership anchor count {block.count(owner)}")
            block = block.replace(owner, guard + owner, 1)
            s = s[:a] + block + s[b:]
        elif count != 1:
            raise SystemExit(f"A1 {name}: FMODE_WRITE guard count {count}")
        block = function_block(s, sig)
        if block.index("if (!(filp->f_mode & FMODE_WRITE))") > block.index("if (!inode_owner_or_capable(inode))"):
            raise SystemExit(f"A1 {name}: guard ordering wrong")
    path.write_text(s)


def patch_d6(root):
    path = root / "fs/fuse/inode.c"
    s = require(path)
    old = "FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |\n\t\tFUSE_NO_OPEN_SUPPORT |"
    new = "FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO | FUSE_ASYNC_DIO |\n\t\tFUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT |"
    if old in s:
        s = replace_once(s, old, new, "D6 restore FUSE_WRITEBACK_CACHE")
    elif new not in s:
        raise SystemExit("D6: FUSE init advertisement anchor missing")
    if "!(arg->flags & FUSE_WRITEBACK_CACHE)" not in s:
        raise SystemExit("D6: passthrough/writeback exclusion missing")
    path.write_text(s)


def patch_d5(root):
    path = root / "fs/f2fs/namei.c"
    s = require(path)
    sig = "static struct inode *f2fs_new_inode(struct inode *dir, umode_t mode)"
    a, b = function_span(s, sig)
    block = s[a:b]
    needle = "\tf2fs_init_extent_tree(inode, NULL);\n"
    age = "\tf2fs_init_age_extent_tree(inode);\n"
    if age not in block:
        if block.count(needle) != 1:
            raise SystemExit(f"D5: new_inode extent-init anchor count {block.count(needle)}")
        block = block.replace(needle, needle + age, 1)
        s = s[:a] + block + s[b:]
    path.write_text(s)


def patch_c7(root):
    path = root / "kernel/sched/core.c"
    s = require(path)
    sig = "static inline void uclamp_rq_dec_id(struct rq *rq, struct task_struct *p,"
    a, b = function_span(s, sig)
    block = s[a:b]
    guard = "\tif (unlikely(!uc_se->active))\n\t\treturn;\n"
    if guard not in block:
        anchor = "\tlockdep_assert_held(&rq->lock);\n"
        if anchor not in block:
            anchor = "\tlockdep_assert_rq_held(rq);\n"
        if anchor not in block:
            raise SystemExit("C7: uclamp lockdep anchor missing")
        block = block.replace(anchor, anchor + "\n" + guard, 1)
        s = s[:a] + block + s[b:]
    if function_block(s, sig).count(guard) != 1:
        raise SystemExit("C7: inactive guard not exactly once")
    path.write_text(s)


def patch_b2(root):
    path = root / "mm/vmscan.c"
    s = require(path)
    sig = "static void lru_gen_age_node(struct pglist_data *pgdat,"
    a, b = function_span(s, sig)
    block = s[a:b]
    if "if (success || !min_ttl || sc->order)" not in block:
        old = "\tif (!success && !sc->order && mutex_trylock(&oom_lock)) {\n"
        if block.count(old) != 1:
            raise SystemExit(f"B2: OOM anchor count {block.count(old)}")
        new = "\tif (success || !min_ttl || sc->order)\n\t\treturn;\n\n\tif (mutex_trylock(&oom_lock)) {\n"
        block = block.replace(old, new, 1)
        s = s[:a] + block + s[b:]
    path.write_text(s)


def patch_d7(root):
    path = root / "drivers/scsi/ufs/ufshcd.c"
    s = require(path)
    sig = "static int ufshcd_devfreq_target(struct device *dev,"
    a, b = function_span(s, sig)
    block = s[a:b]
    if block.count("pm_runtime_get_noresume(hba->dev);") > 1 or block.count("pm_runtime_put(hba->dev);") > 1:
        start = block.find("\tpm_runtime_get_noresume(hba->dev);")
        trace = block.find("\n\ttrace_ufshcd_profile_clk_scaling", start)
        if start < 0 or trace < 0:
            raise SystemExit("D7: unable to bound duplicated PM section")
        canonical = (
            "\tpm_runtime_get_noresume(hba->dev);\n"
            "\tif (!pm_runtime_active(hba->dev)) {\n"
            "\t\tpm_runtime_put_noidle(hba->dev);\n"
            "\t\tret = -EAGAIN;\n"
            "\t\tgoto out;\n"
            "\t}\n"
            "\tstart = ktime_get();\n"
            "\tret = ufshcd_devfreq_scale(hba, scale_up);\n"
            "\tpm_runtime_put(hba->dev);\n"
        )
        block = block[:start] + canonical + block[trace:]
        s = s[:a] + block + s[b:]
    block = function_block(s, sig)
    if block.count("pm_runtime_get_noresume(hba->dev);") != 1:
        raise SystemExit("D7: expected exactly one runtime-PM get")
    if block.count("pm_runtime_put(hba->dev);") != 1:
        raise SystemExit("D7: expected exactly one runtime-PM put")
    path.write_text(s)


def patch_d9(root):
    path = root / "techpack/display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"
    s = require(path)
    old = "\t/* send lpm bl cmd */\n\tss_send_cmd(vdd, TX_LPM_BL_CMD);\n\ta52_aod_lpm_bl_cache(vdd, lpm_bl_level);\n"
    new = "\t/* Cache only a brightness command that the panel accepted. */\n\tif (!ss_send_cmd(vdd, TX_LPM_BL_CMD))\n\t\ta52_aod_lpm_bl_cache(vdd, lpm_bl_level);\n"
    if old in s:
        s = replace_once(s, old, new, "D9 successful AOD command cache")
    elif new not in s:
        raise SystemExit("D9: AOD TX/cache anchor missing")
    path.write_text(s)


def patch_c5(root):
    path = root / "kernel/sched/fair.c"
    s = require(path)
    sig = "static void yield_task_fair(struct rq *rq)"
    a, b = function_span(s, sig)
    block = s[a:b]
    if "eevdf_cancel_protect_slice(se);" not in block:
        old = "\tif (unlikely(sched_eevdf_enabled)) {\n\t\tu64 slice = se->slice ? se->slice : eevdf_base_slice();\n\n"
        new = old + "\t\teevdf_cancel_protect_slice(se);\n"
        if block.count(old) != 1:
            raise SystemExit(f"C5: yield EEVDF anchor count {block.count(old)}")
        block = block.replace(old, new, 1)
        s = s[:a] + block + s[b:]
    path.write_text(s)


def patch_c4(root):
    path = root / "kernel/sched/fair.c"
    s = require(path)
    sig = "static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,"
    block = function_block(s, sig)
    if "bool eevdf_reweight = eevdf && old_weight != weight;" in block:
        return
    old = "\tbool eevdf = unlikely(sched_eevdf_enabled);\n\tbool curr = cfs_rq->curr == se;\n\tunsigned long old_weight = se->load.weight;\n"
    new = old + "\tbool eevdf_reweight = eevdf && old_weight != weight;\n"
    if block.count(old) != 1:
        raise SystemExit("C4: reweight declaration anchor mismatch")
    block = block.replace(old, new, 1)

    old_update = (
        "\t\tif (eevdf)\n"
        "\t\t\tupdate_curr(cfs_rq);\n"
        "\t\telse if (curr)\n"
        "\t\t\tupdate_curr(cfs_rq);\n\n"
        "\t\tif (eevdf) {\n"
    )
    new_update = (
        "\t\tif (curr)\n"
        "\t\t\tupdate_curr(cfs_rq);\n\n"
        "\t\tif (eevdf_reweight) {\n"
    )
    if block.count(old_update) != 1:
        raise SystemExit("C4: current/reweight anchor mismatch")
    block = block.replace(old_update, new_update, 1)
    block = block.replace("\tif (eevdf && old_weight != weight) {\n", "\tif (eevdf_reweight) {\n", 1)
    block = block.replace("\t\tif (eevdf) {\n\t\t\tse->vlag = vlag;\n",
                          "\t\tif (eevdf_reweight) {\n\t\t\tse->vlag = vlag;\n", 1)
    block = block.replace("\t\tif (eevdf) {\n\t\t\tif (!curr)\n",
                          "\t\tif (eevdf_reweight) {\n\t\t\tif (!curr)\n", 1)
    block = block.replace("\t} else if (eevdf) {\n\t\tEEVDF_STAT_INC(reweight_offrq);",
                          "\t} else if (eevdf_reweight) {\n\t\tEEVDF_STAT_INC(reweight_offrq);", 1)
    if "se->min_slice = se->slice ? se->slice : eevdf_base_slice();" not in block:
        raise SystemExit("C4: P109 min_slice coherence missing")
    s = replace_function(s, sig, block)
    path.write_text(s)


def patch_cass(root):
    path = root / "kernel/sched/cass.c"
    s = require(path)

    old_struct = """struct cass_cpu_cand {
    int cpu;
    unsigned int exit_lat;
    unsigned long cap;
    unsigned long cap_max;
    unsigned long util;
};"""
    new_struct = """struct cass_cpu_cand {
    int cpu;
    unsigned int exit_lat;
    unsigned long cap;
    unsigned long cap_max;
    unsigned long util;
    bool vendor_fit;
    bool rtg_preferred;
};"""
    if old_struct in s:
        s = replace_once(s, old_struct, new_struct, "C2 candidate fields")
    elif new_struct not in s:
        raise SystemExit("C2: candidate struct anchor missing")

    better = r'''static __always_inline
bool cass_cpu_better(const struct cass_cpu_cand *a,
                     const struct cass_cpu_cand *b,
                     unsigned long p_util, unsigned long uc_min,
                     bool prefer_perf,
                     int this_cpu, int prev_cpu, bool sync)
{
#define cass_cmp(a, b) ({ res = (long)(a) - (long)(b); })
#define cass_eq(a, b)  ({ res = (long)((a) == (b)); })
    long res;

    if (cass_cmp(a->cap_max >= uc_min, b->cap_max >= uc_min))
        goto done;

    if (cass_cmp(a->vendor_fit, b->vendor_fit))
        goto done;

    if (prefer_perf && cass_cmp(a->rtg_preferred, b->rtg_preferred))
        goto done;

    if (cass_cmp(a->cap_max >= p_util, b->cap_max >= p_util))
        goto done;

    /* Normal fitting work prefers the smallest adequate CPU. */
    if (!prefer_perf && a->vendor_fit && b->vendor_fit &&
        cass_cmp(b->cap_max, a->cap_max))
        goto done;

    /* Explicitly boosted/RTG work retains higher capacity when tied. */
    if (prefer_perf && cass_cmp(a->cap_max, b->cap_max))
        goto done;

    if (cass_cmp(b->util, a->util))
        goto done;

    if (uc_min && cass_cmp(!!a->exit_lat, !!b->exit_lat))
        goto done;

    if (sync &&
        (cass_eq(a->cpu, this_cpu) || !cass_cmp(b->cpu, this_cpu)))
        goto done;

    if (cass_cmp(a->cap, b->cap))
        goto done;

    if (cass_cmp(b->exit_lat, a->exit_lat))
        goto done;

    if (cass_eq(a->cpu, prev_cpu) || !cass_cmp(b->cpu, prev_cpu))
        goto done;

    if (cass_cmp(cpus_share_cache(a->cpu, prev_cpu),
                 cpus_share_cache(b->cpu, prev_cpu)))
        goto done;

done:
    return res > 0;
#undef cass_cmp
#undef cass_eq
}'''
    s = replace_function(s, "static __always_inline\nbool cass_cpu_better(", better)

    best = r'''static int cass_best_cpu(struct task_struct *p, int prev_cpu, bool sync)
{
    struct cass_cpu_cand cands[2], *best = cands;
    int this_cpu = raw_smp_processor_id();
    unsigned long p_util = task_util_est(p);
    unsigned long uc_min = 0;
    unsigned long uc_max = SCHED_CAPACITY_SCALE;
    int task_boost = per_task_boost(p);
    bool prefer_perf;
    bool has_idle = false;
    bool have_candidate = false;
    int fallback_cpu = -1;
    int cidx = 0, cpu;

    if (uclamp_is_used()) {
        uc_min = uclamp_eff_value(p, UCLAMP_MIN);
        uc_max = uclamp_eff_value(p, UCLAMP_MAX);
        p_util = min(p_util, uc_max);
    }

    /*
     * Reuse Samsung/WALT placement signals instead of restoring the
     * schedtune margin inside uclamp_task(), which would double boost.
     */
    prefer_perf = schedtune_task_boost(p) > 0 ||
                  task_boost_policy(p) == SCHED_BOOST_ON_BIG ||
                  task_boost > 0 ||
                  task_skip_min_cpu(p) ||
                  task_rtg_high_prio(p);

    pr_info_once("A52 CASS P162: isolation + Samsung boost/RTG + efficient fit policy active\n");
    rcu_read_lock();

    for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask) {
        struct cass_cpu_cand *curr = &cands[cidx];
        struct cpuidle_state *idle_state;
        struct rq *rq = cpu_rq(cpu);
        int cluster_pref;
        unsigned long util;

        if (fallback_cpu < 0)
            fallback_cpu = cpu;

        if (!(p->flags & PF_KTHREAD) && cpu_isolated(cpu))
            continue;

        curr->cpu = cpu;
        curr->cap_max = max_t(unsigned long, capacity_orig_of(cpu), 1UL);
        curr->vendor_fit = task_fits_max(p, cpu);
        cluster_pref = rq->cluster ? preferred_cluster(rq->cluster, p) : -1;
        curr->rtg_preferred = cluster_pref > 0;

        if ((sync && cpu == this_cpu && rq->nr_running == 1) ||
            available_idle_cpu(cpu)) {
            if (!uc_min && !prefer_perf) {
                if (!has_idle)
                    best = curr;
                has_idle = true;
            }

            curr->exit_lat = 1;
            idle_state = idle_get_state(rq);
            if (idle_state)
                curr->exit_lat += idle_state->exit_latency;
        } else {
            if (has_idle && !prefer_perf)
                continue;
            curr->exit_lat = 0;
        }

        util = cass_walt_cpu_util(cpu, this_cpu, sync);

        /*
         * Preserve shipped P115 behavior. C3 has a separately agreed
         * queued/running-state refinement and is not folded into this bundle.
         */
        if (cpu != task_cpu(p))
            util += p_util;

        if (util < uc_min)
            util = uc_min;

        curr->cap = max_t(unsigned long, capacity_of(cpu), 1UL);
        curr->util = util * SCHED_CAPACITY_SCALE / curr->cap;

        if (!have_candidate ||
            cass_cpu_better(curr, best, p_util, uc_min, prefer_perf,
                            this_cpu, prev_cpu, sync)) {
            best = curr;
            have_candidate = true;
            cidx ^= 1;
        }
    }

    rcu_read_unlock();

    if (!have_candidate)
        return fallback_cpu >= 0 ? fallback_cpu : prev_cpu;

    return best->cpu;
}'''
    s = replace_function(s, "static int cass_best_cpu(struct task_struct *p, int prev_cpu, bool sync)", best)
    path.write_text(s)


def patch_a6(root):
    gmu_p = root / "drivers/gpu/msm/kgsl_gmu.c"
    hdr_p = root / "drivers/gpu/msm/kgsl_gmu.h"
    pwr_p = root / "drivers/gpu/msm/kgsl_pwrctrl.c"
    disp_p = root / "drivers/gpu/msm/adreno_dispatch.c"
    g = require(gmu_p)
    h = require(hdr_p)
    p = require(pwr_p)
    d = require(disp_p)

    marker = "static bool a52_gpu_uv_enabled __read_mostly = true;"
    if marker not in g:
        anchor = "static const char gfx_res_id[] = \"gfx.lvl\";\n"
        g = replace_once(g, anchor, marker + "\n\n" + anchor, "A6 global gate")

    for fn in (
        "a52_a619_uv_p1_adjust_top_opp",
        "a52_a619_uv_p2_adjust_650_opp",
        "a52_a619_uv_p3_adjust_565_opp",
    ):
        a, b = function_span(g, f"static void {fn}(")
        block = g[a:b]
        if "READ_ONCE(a52_gpu_uv_enabled)" not in block:
            old = "\tif (!adreno_is_a619(ADRENO_DEVICE(device)) || num_freqs < 2)\n\t\treturn;\n"
            new = "\tif (!READ_ONCE(a52_gpu_uv_enabled) ||\n\t\t\t!adreno_is_a619(ADRENO_DEVICE(device)) || num_freqs < 2)\n\t\treturn;\n"
            if block.count(old) != 1:
                raise SystemExit(f"A6 {fn}: gate anchor mismatch")
            block = block.replace(old, new, 1)
            g = g[:a] + block + g[b:]

    a, b = function_span(g, "static void a52_a619_uv_p1_adjust_top_opp(")
    block = g[a:b]
    if "P2/P3 own these exact OPPs" not in block:
        anchor = "\tif (!freq_tbl[top] || !vlvl_tbl[top] || gfx_arc->num < 2)\n\t\treturn;\n\n"
        skip = (
            "\t/* P2/P3 own these exact OPPs; never lower the same level twice. */\n"
            "\tif (freq_tbl[top] == 650000000U || freq_tbl[top] == 565000000U)\n"
            "\t\treturn;\n\n"
        )
        if block.count(anchor) != 1:
            raise SystemExit("A6: P1 overlap anchor missing")
        block = block.replace(anchor, skip + anchor, 1)
        g = g[:a] + block + g[b:]

    helper = r'''
/*
 * P162 A619 UV fail-safe. Caller holds device->mutex.
 * With the gate disabled rpmh_arc_votes_init reconstructs pristine GX votes
 * from the stock OPP and runtime ARC tables.
 */
static int a52_a619_uv_rebuild_gx_votes_locked(struct kgsl_device *device)
{
	struct gmu_device *gmu = KGSL_GMU_DEVICE(device);
	struct rpmh_arc_vals gfx_arc, mx_arc;
	int ret;

	if (!gmu || !adreno_is_a619(ADRENO_DEVICE(device)))
		return 0;

	ret = rpmh_arc_cmds(gmu, &gfx_arc, gfx_res_id);
	if (ret)
		return ret;

	ret = rpmh_arc_cmds(gmu, &mx_arc, mx_res_id);
	if (ret)
		return ret;

	return rpmh_arc_votes_init(device, gmu, &gfx_arc, &mx_arc,
				   GPU_ARC_VOTE);
}

int a52_a619_uv_disable_locked(struct kgsl_device *device)
{
	int ret;

	if (!adreno_is_a619(ADRENO_DEVICE(device)))
		return 0;

	if (!READ_ONCE(a52_gpu_uv_enabled))
		return 0;

	WRITE_ONCE(a52_gpu_uv_enabled, false);
	ret = a52_a619_uv_rebuild_gx_votes_locked(device);
	if (ret) {
		WRITE_ONCE(a52_gpu_uv_enabled, true);
		return ret;
	}

	dev_warn(device->dev,
		 "A52 GPU UV P162: disabled and stock GX vote table rebuilt\n");
	return 0;
}

bool a52_a619_uv_is_enabled(void)
{
	return READ_ONCE(a52_gpu_uv_enabled);
}

'''
    if "a52_a619_uv_disable_locked" not in g:
        pos = g.find("static irqreturn_t gmu_irq_handler(int irq, void *data)")
        if pos < 0:
            raise SystemExit("A6: GMU helper insertion anchor missing")
        g = g[:pos] + helper + g[pos:]

    decl = "int a52_a619_uv_disable_locked(struct kgsl_device *device);\nbool a52_a619_uv_is_enabled(void);\n"
    if "a52_a619_uv_disable_locked" not in h:
        anchor = "#endif /* __KGSL_GMU_H */"
        if anchor not in h:
            raise SystemExit("A6: kgsl_gmu.h footer missing")
        h = h.replace(anchor, decl + "\n" + anchor, 1)

    if '#include "kgsl_gmu.h"' not in p:
        p = p.replace('#include "kgsl_device.h"\n', '#include "kgsl_device.h"\n#include "kgsl_gmu.h"\n', 1)

    attr_code = r'''
static ssize_t a52_gpu_uv_enable_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	return scnprintf(buf, PAGE_SIZE, "%u\n",
			 a52_a619_uv_is_enabled() ? 1 : 0);
}

static ssize_t a52_gpu_uv_enable_store(struct device *dev,
		struct device_attribute *attr, const char *buf, size_t count)
{
	struct kgsl_device *device = dev_get_drvdata(dev);
	unsigned int val;
	int ret;

	ret = kstrtouint(buf, 0, &val);
	if (ret)
		return ret;
	if (val > 1)
		return -EINVAL;

	/* One-way fail-safe until reboot: never re-enable after a fault. */
	if (val)
		return a52_a619_uv_is_enabled() ? count : -EPERM;

	mutex_lock(&device->mutex);
	ret = a52_a619_uv_disable_locked(device);
	mutex_unlock(&device->mutex);

	return ret ? ret : count;
}

static DEVICE_ATTR_RW(a52_gpu_uv_enable);
'''
    if "static DEVICE_ATTR_RW(a52_gpu_uv_enable);" not in p:
        anchor = "static DEVICE_ATTR_RW(gpuclk);\n"
        if anchor not in p:
            raise SystemExit("A6: pwrctrl gpuclk attr anchor missing")
        p = p.replace(anchor, attr_code + "\n" + anchor, 1)
        p = replace_once(p, "\t&dev_attr_gpuclk.attr,\n",
                         "\t&dev_attr_a52_gpu_uv_enable.attr,\n\t&dev_attr_gpuclk.attr,\n",
                         "A6 sysfs list")

    if '#include "kgsl_gmu.h"' not in d:
        d = d.replace('#include "kgsl_gmu_core.h"\n',
                      '#include "kgsl_gmu_core.h"\n#include "kgsl_gmu.h"\n', 1)

    if "P162 UV fail-safe before GPU reset" not in d:
        anchor = "\tif (gpudev->reset)\n\t\tret = gpudev->reset(device, fault);\n"
        hook = (
            "\t/* P162 UV fail-safe before GPU reset: cold boot uses stock GX votes. */\n"
            "\tif (gmu_core_isenabled(device)) {\n"
            "\t\tint uv_ret = a52_a619_uv_disable_locked(device);\n\n"
            "\t\tif (uv_ret)\n"
            "\t\t\tdev_err(device->dev,\n"
            "\t\t\t\t\"A52 GPU UV P162: failed to restore stock votes: %d\\n\",\n"
            "\t\t\t\tuv_ret);\n"
            "\t}\n\n"
        )
        if anchor not in d:
            raise SystemExit("A6: dispatcher reset anchor missing")
        d = d.replace(anchor, hook + anchor, 1)

    gmu_p.write_text(g)
    hdr_p.write_text(h)
    pwr_p.write_text(p)
    disp_p.write_text(d)


def audits(root):
    checks = []

    f2 = require(root / "fs/f2fs/file.c")
    for fn in ("start_atomic_write", "commit_atomic_write", "start_volatile_write",
               "release_volatile_write", "abort_volatile_write"):
        block = function_block(f2, f"static int f2fs_ioc_{fn}(struct file *filp)")
        checks.append((f"A1:{fn}", block.count("if (!(filp->f_mode & FMODE_WRITE))") == 1))

    fuse = require(root / "fs/fuse/inode.c")
    checks.append(("D6:writeback-advertised", "FUSE_WRITEBACK_CACHE | FUSE_NO_OPEN_SUPPORT" in fuse))
    checks.append(("D6:passthrough-excludes-writeback", "!(arg->flags & FUSE_WRITEBACK_CACHE)" in fuse))

    nb = function_block(require(root / "fs/f2fs/namei.c"),
                        "static struct inode *f2fs_new_inode(struct inode *dir, umode_t mode)")
    checks.append(("D5:new-inode-age-tree", nb.count("f2fs_init_age_extent_tree(inode);") == 1))

    ub = function_block(require(root / "kernel/sched/core.c"),
                        "static inline void uclamp_rq_dec_id(struct rq *rq, struct task_struct *p,")
    checks.append(("C7:inactive-guard", ub.count("if (unlikely(!uc_se->active))") == 1))

    fair = require(root / "kernel/sched/fair.c")
    rw = function_block(fair, "static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,")
    checks.append(("C4:weight-change-gate", "bool eevdf_reweight = eevdf && old_weight != weight;" in rw))
    y = function_block(fair, "static void yield_task_fair(struct rq *rq)")
    checks.append(("C5:yield-cancels-protection", "eevdf_cancel_protect_slice(se);" in y))

    cass = require(root / "kernel/sched/cass.c")
    checks.append(("C1:isolated-filter", "!(p->flags & PF_KTHREAD) && cpu_isolated(cpu)" in cass))
    for needle in ("task_fits_max(p, cpu)", "per_task_boost(p)", "task_boost_policy(p)",
                   "schedtune_task_boost(p)", "task_skip_min_cpu(p)",
                   "task_rtg_high_prio(p)", "preferred_cluster(rq->cluster, p)"):
        checks.append((f"C2:{needle}", needle in cass))

    age = function_block(require(root / "mm/vmscan.c"),
                         "static void lru_gen_age_node(struct pglist_data *pgdat,")
    checks.append(("B2:min-ttl-oom-gate", "if (success || !min_ttl || sc->order)" in age))

    uf = function_block(require(root / "drivers/scsi/ufs/ufshcd.c"),
                        "static int ufshcd_devfreq_target(struct device *dev,")
    checks.append(("D7:one-pm-get", uf.count("pm_runtime_get_noresume(hba->dev);") == 1))
    checks.append(("D7:one-pm-put", uf.count("pm_runtime_put(hba->dev);") == 1))

    panel = require(root / "techpack/display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c")
    checks.append(("D9:cache-success-only",
                   "if (!ss_send_cmd(vdd, TX_LPM_BL_CMD))\n\t\ta52_aod_lpm_bl_cache(vdd, lpm_bl_level);" in panel))

    gmu = require(root / "drivers/gpu/msm/kgsl_gmu.c")
    disp = require(root / "drivers/gpu/msm/adreno_dispatch.c")
    pwr = require(root / "drivers/gpu/msm/kgsl_pwrctrl.c")
    checks.append(("A6:global-gate", "static bool a52_gpu_uv_enabled __read_mostly = true;" in gmu))
    checks.append(("A6:no-overlap", "P2/P3 own these exact OPPs" in gmu))
    checks.append(("A6:stock-rebuild", "a52_a619_uv_rebuild_gx_votes_locked" in gmu))
    checks.append(("A6:fault-disable", "P162 UV fail-safe before GPU reset" in disp))
    checks.append(("A6:manual-off-switch", "DEVICE_ATTR_RW(a52_gpu_uv_enable)" in pwr))

    bad = [name for name, ok in checks if not ok]
    if bad:
        raise SystemExit("P162 audit failures: " + ", ".join(bad))
    for name, _ in checks:
        print(f"[PASS] {name}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()
    patch_a1(root)
    patch_d6(root)
    patch_d5(root)
    patch_c7(root)
    patch_c4(root)
    patch_c5(root)
    patch_cass(root)
    patch_b2(root)
    patch_a6(root)
    patch_d7(root)
    patch_d9(root)
    audits(root)
    print("P162 integrated security/stability/efficiency bundle: PASS")
    print(f"baseline={BASELINE}")
    print(f"A1_upstream={UPSTREAM_A1}")
    print("fixes=A1,A6,B2,C1,C2,C4,C5,C7,D5,D6,D7,D9")


if __name__ == "__main__":
    main()
