    a, b = function_bounds(text, sig)
    fn = text[a:b]
    anchor = "\t\tlist_for_each_entry(cur_bcm, &cur_bcm_clist[i], link) {\n"
    if anchor not in fn:
        raise SystemExit("Phase427 bus-fabric BCM loop missing")
    fn = fn.replace(anchor, anchor + "\t\t\ta52_p427_bcm_shadow_update(cur_bcm);\n", 1)
    text = text[:a] + fn + text[b:]
    text += "\n/* " + MARK + ": selected committed BCM shadows. */\n"
    return text


HWC_BLOCK = r'''
/* A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1
 * Road-to-memory snapshot.  The pre-trigger call executes after all DSI DMA
 * programming and before SW_TRIGGER.  It performs only normal-RAM loads and
 * MMIO reads.  No clock, regulator, RPMh, bus-vote, SMMU or RSC write occurs.
 */
#define A52_P427_TCS_BASE     0x1c00U
#define A52_P427_TCS_STRIDE   0x02a0U
#define A52_P427_CMD_STRIDE   0x0014U
#define A52_P427_MAX_CMDS     8U
#define A52_P427_BUS_SLOTS    4U
#define A52_P427_BCM_SLOTS    7U
#define A52_P427_SNAPS        2U

struct a52_p427_tcs_cmd {
	u32 addr;
	u32 data;
	u32 status;
};
struct a52_p427_tcs {
	u32 control;
	u32 status;
	u32 enable;
	u32 wait;
	struct a52_p427_tcs_cmd cmd[A52_P427_MAX_CMDS];
};
struct a52_p427_bus_sample {
	u32 valid;
	u32 handle;
	s32 curr;
	u32 num_paths;
	u64 ab;
	u64 ib;
};
struct a52_p427_bcm_sample {
	u32 valid;
	u32 addr;
	u64 vec_a;
	u64 vec_b;
};
struct a52_p427_road_snapshot {
	u64 ns;
	u32 valid;
	u32 child_cfg;
	u32 num_tcs;
	u32 ncpt;
	s32 rsc_state;
	u32 gcc40;
	u32 gcc44;
	u32 tbu_valid;
	u32 tbu_status;
	u32 sacr_seen;
	u32 sacr_before;
	u32 sacr_after;
	u32 sacr_current;
	struct a52_p427_tcs tcs[2];
	struct a52_p427_bus_sample bus[A52_P427_BUS_SLOTS];
	struct a52_p427_bcm_sample bcm[A52_P427_BCM_SLOTS];
};

static struct a52_p427_road_snapshot a52_p427_road[A52_P427_SNAPS];
static atomic_t a52_p427_taken[A52_P427_SNAPS] = {
	ATOMIC_INIT(0), ATOMIC_INIT(0)
};
extern void a52_p336_qsmmuv500_snapshot(u32 *seen, u32 *sacr_before,
		u32 *sacr_after, u32 *sacr_current, u32 *tbu_valid, u32 *tbu_status);
extern void a52_p427_bus_get(u32 slot, u32 *valid, u32 *handle, s32 *curr,
		u32 *num_paths, u64 *ab, u64 *ib);
extern void a52_p427_bcm_get(u32 slot, u32 *valid, u32 *addr,
		u64 *vec_a, u64 *vec_b);
extern int get_sde_rsc_current_state(int rsc_index);

static void a52_p427_read_tcs(struct a52_p427_road_snapshot *s, u32 t)
{
	void __iomem *b = a52_p424_rsc_drv;
	u32 base = A52_P427_TCS_BASE + t * A52_P427_TCS_STRIDE;
	u32 n;

	if (!b || t >= 2)
		return;
	s->tcs[t].wait = a52_p424_r(b, base + 0x10);
	s->tcs[t].control = a52_p424_r(b, base + 0x14);
	s->tcs[t].status = a52_p424_r(b, base + 0x18);
	s->tcs[t].enable = a52_p424_r(b, base + 0x1c);
	for (n = 0; n < min_t(u32, s->ncpt, A52_P427_MAX_CMDS); n++) {
		u32 c = base + 0x30 + n * A52_P427_CMD_STRIDE;
		s->tcs[t].cmd[n].addr = a52_p424_r(b, c + 0x04);
		s->tcs[t].cmd[n].data = a52_p424_r(b, c + 0x08);
		s->tcs[t].cmd[n].status = a52_p424_r(b, c + 0x0c);
	}
}

void a52_p427_road_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	struct a52_p427_road_snapshot *s;
	u32 i;

	if (point >= A52_P427_SNAPS || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p427_taken[point], 0, 1) != 0)
		return;

	s = &a52_p427_road[point];
	memset(s, 0, sizeof(*s));
	s->ns = ktime_get_boottime_ns();
	s->valid = 1;
	s->child_cfg = a52_p424_r(a52_p424_rsc_drv, 0x0c);
	s->num_tcs = (s->child_cfg >> 6) & 0x3f;
	s->ncpt = (s->child_cfg >> 27) & 0x1f;
	s->rsc_state = get_sde_rsc_current_state(0);
	s->gcc40 = a52_p424_r(a52_p427_hf0_gcc, 0x40);
	s->gcc44 = a52_p424_r(a52_p427_hf0_gcc, 0x44);
	a52_p336_qsmmuv500_snapshot(&s->sacr_seen, &s->sacr_before,
		&s->sacr_after, &s->sacr_current, &s->tbu_valid, &s->tbu_status);

	for (i = 0; i < min_t(u32, s->num_tcs, 2U); i++)
		a52_p427_read_tcs(s, i);
	for (i = 0; i < A52_P427_BUS_SLOTS; i++)
		a52_p427_bus_get(i, &s->bus[i].valid, &s->bus[i].handle,
			&s->bus[i].curr, &s->bus[i].num_paths,
			&s->bus[i].ab, &s->bus[i].ib);
	for (i = 0; i < A52_P427_BCM_SLOTS; i++)
		a52_p427_bcm_get(i, &s->bcm[i].valid, &s->bcm[i].addr,
			&s->bcm[i].vec_a, &s->bcm[i].vec_b);
	smp_wmb();
}
EXPORT_SYMBOL_GPL(a52_p427_road_snapshot);

void a52_p427_road_dump(void)
{
	u32 p, t, n, i;

	for (p = 0; p < A52_P427_SNAPS; p++) {
		const struct a52_p427_road_snapshot *s = &a52_p427_road[p];
		if (!READ_ONCE(s->valid))
			continue;
		a52_ackfr_record("P427 R p=%u cf=%x nt=%u nc=%u rs=%d",
			p, s->child_cfg, s->num_tcs, s->ncpt, s->rsc_state);
		a52_ackfr_record("P427 G p=%u g0=%x g1=%x tv=%u ts=%x",
			p, s->gcc40, s->gcc44, s->tbu_valid, s->tbu_status);
		a52_ackfr_record("P427 A p=%u v=%u b=%x a=%x c=%x",
			p, s->sacr_seen, s->sacr_before, s->sacr_after,
			s->sacr_current);
		for (t = 0; t < min_t(u32, s->num_tcs, 2U); t++) {
			a52_ackfr_record("P427 Q p=%u t=%u c=%x e=%x w=%x s=%x",
				p, t, s->tcs[t].control, s->tcs[t].enable,
				s->tcs[t].wait, s->tcs[t].status);
			for (n = 0; n < min_t(u32, s->ncpt, A52_P427_MAX_CMDS); n++)
				a52_ackfr_record("P427 T p=%u t=%u n=%u a=%x d=%x s=%x",
					p, t, n, s->tcs[t].cmd[n].addr,
					s->tcs[t].cmd[n].data,
					s->tcs[t].cmd[n].status);
		}
		for (i = 0; i < A52_P427_BUS_SLOTS; i++) {
			a52_ackfr_record("P427 U0 p=%u s=%u v=%u h=%u c=%d n=%u",
				p, i, s->bus[i].valid, s->bus[i].handle,
				s->bus[i].curr, s->bus[i].num_paths);
			a52_ackfr_record("P427 U1 p=%u s=%u a=%llx i=%llx",
				p, i, (unsigned long long)s->bus[i].ab,
				(unsigned long long)s->bus[i].ib);
		}
		for (i = 0; i < A52_P427_BCM_SLOTS; i++) {
			a52_ackfr_record("P427 B0 p=%u s=%u v=%u x=%x",
				p, i, s->bcm[i].valid, s->bcm[i].addr);
			a52_ackfr_record("P427 B1 p=%u s=%u a=%llx b=%llx",
				p, i, (unsigned long long)s->bcm[i].vec_a,
				(unsigned long long)s->bcm[i].vec_b);
		}
	}
}
EXPORT_SYMBOL_GPL(a52_p427_road_dump);
'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE424_PAIRED_HW_SNAPSHOT_V1",
        "a52_p424_rsc_drv = ioremap(A52_P424_RSC_DRV_PHYS, 0x2000);",
        "a52_p424_snapshot(ctrl);",
        "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
    ):
        if token not in text:
            raise SystemExit("Phase427 HWC prerequisite missing: " + token)

    text = one(text,
               "a52_p424_rsc_drv = ioremap(A52_P424_RSC_DRV_PHYS, 0x2000);",
               "a52_p424_rsc_drv = ioremap(A52_P424_RSC_DRV_PHYS, 0x2400);",
               "RSC map growth")

    pre_init = "static int __init a52_p424_map_init(void)\n"
    text = one(text, pre_init,
               "#define A52_P427_HF0_GCC_PHYS 0x001b7000ULL\n"
               "static void __iomem *a52_p427_hf0_gcc;\n\n" + pre_init,
               "HF0 GCC declaration")
    init_anchor = "\ta52_p424_rsc_wrp = ioremap(A52_P424_RSC_WRP_PHYS, 0x100);\n"
    text = one(text, init_anchor,
               init_anchor + "\ta52_p427_hf0_gcc = ioremap(A52_P427_HF0_GCC_PHYS, 0x1000);\n",
               "HF0 GCC map")

    # HWC_BLOCK refers to Phase424 globals/helpers, so insert after its dump
    # function and before the first unrelated DSI hardware routine.
    anchor = "EXPORT_SYMBOL_GPL(a52_p424_dump_snapshot);\n"
    text = one(text, anchor, anchor + "\n" + HWC_BLOCK + "\n",
               "road snapshot insertion")

    old = '''		if (a52_p421_target_active()) {
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
			a52_p424_snapshot(ctrl);
		}
'''
    new = '''		if (a52_p421_target_active()) {
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
			a52_p424_snapshot(ctrl);
			a52_p427_road_snapshot(ctrl, 0U);
		}
'''
    text = one(text, old, new, "S07 road snapshot")
    text += "\n/* " + MARK + ": S07/pre and post-wait road-to-memory snapshots. */\n"
    return text


DSI_BLOCK = r'''
/* A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1 */
struct a52_p427_cmdmap {
	u32 valid;
	u64 gem_iova;
	u64 field_iova;
	u64 iova_phys;
	u64 page_phys;
	u64 first8;
};
static struct a52_p427_cmdmap a52_p427_cmdmap;
static atomic_t a52_p427_posted = ATOMIC_INIT(0);
extern void a52_p427_road_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);
extern void a52_p427_road_dump(void);
extern void a52_p427_smmu_dump(void);

static void a52_p427_capture_cmdmap(struct dsi_ctrl *dsi_ctrl)
{
	struct msm_gem_address_space *aspace;
	struct device *aspace_dev = NULL;
	struct iommu_domain *domain = NULL;
	struct msm_gem_object *bo = NULL;
	u64 iova = 0;
	u64 first8 = 0;

	if (!dsi_ctrl || !dsi_ctrl->tx_cmd_buf)
		return;
	aspace = dsi_ctrl_get_aspace(dsi_ctrl, MSM_SMMU_DOMAIN_UNSECURE);
	if (aspace)
		aspace_dev = msm_gem_get_aspace_device(aspace);
	if (aspace_dev)
		domain = iommu_get_domain_for_dev(aspace_dev);
	if (aspace)
		iova = msm_gem_iova(dsi_ctrl->tx_cmd_buf, aspace);

	bo = to_msm_bo(dsi_ctrl->tx_cmd_buf);
