		irq = smmu->irqs[smmu->num_global_irqs + cfg->irptndx];
'''
    text = one(text, old, new, "post-program CB2 TLBI")

    old = '''		if (a52_p422_dev_has_sid800(dev)) {
			a52_p422_irq_cache.domain = domain;
			a52_p422_irq_cache.smmu = smmu;
			a52_p422_irq_cache.sid = 0x800;
			a52_p422_irq_cache.cbndx = cfg->cbndx;
			a52_p422_irq_cache.irptndx = cfg->irptndx;
			a52_p422_irq_cache.irq = irq;
			a52_p422_irq_cache.request_ret = ret;
			WRITE_ONCE(a52_p422_irq_cache.valid, true);
		}
'''
    new = old[:-2] + '''
			if (a52_p427_reused_cb2(dev, cfg->cbndx)) {
				a52_p427_handoff.domain = domain;
				a52_p427_handoff.irptndx = cfg->irptndx;
				a52_p427_handoff.irq = irq;
				a52_p427_handoff.request_ret = ret;
			}
		}
'''
    text = one(text, old, new, "Phase422 watcher follows reused CB")

    old = '''out_clear_smmu:
	if (!dynamic)
		__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
	smmu_domain->smmu = NULL;
'''
    new = '''out_clear_smmu:
	if (!dynamic && !a52_p427_reused_cb2(dev, cfg->cbndx))
		__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
	smmu_domain->smmu = NULL;
'''
    text = one(text, old, new, "failed-init pinned CB preservation")

    text += "\n/* " + MARK + ": exact SID800 firmware-CB reuse + postmortem state. */\n"
    return text


BUS_ARB_BLOCK = r'''
/* A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1
 * Read-only software shadow of the display bus clients.  The shadow is updated
 * after the real msm_bus transaction path has completed.  F0 reads only these
 * normal-RAM fields, so it does not take msm_bus_adhoc_lock or send RPMh work.
 */
#define A52_P427_BUS_SLOTS 4U
struct a52_p427_bus_shadow {
	u32 valid;
	u32 handle;
	s32 curr;
	u32 num_paths;
	u64 ab;
	u64 ib;
};
static struct a52_p427_bus_shadow a52_p427_bus_shadow[A52_P427_BUS_SLOTS];

static int a52_p427_bus_slot(const char *name)
{
	if (!name)
		return -1;
	if (!strcmp(name, "mdss_sde"))
		return 0;
	if (!strcmp(name, "disp_rsc_mnoc_llcc"))
		return 1;
	if (!strcmp(name, "disp_rsc_ebi"))
		return 2;
	if (strnstr(name, "proxy", strlen(name)))
		return 3;
	return -1;
}

static void a52_p427_bus_shadow_update(u32 handle,
				       struct msm_bus_client *client)
{
	struct msm_bus_scale_pdata *pdata;
	struct a52_p427_bus_shadow *s;
	int slot;
	int idx;
	u64 ab = 0, ib = 0;
	u32 paths = 0;

	if (!client || !client->pdata)
		return;
	pdata = client->pdata;
	slot = a52_p427_bus_slot(pdata->name);
	if (slot < 0)
		return;

	idx = READ_ONCE(client->curr);
	if (pdata->usecase)
		paths = pdata->usecase->num_paths;
	if (idx >= 0 && idx < pdata->num_usecases && paths &&
	    pdata->usecase[idx].vectors) {
		ab = pdata->usecase[idx].vectors[0].ab;
		ib = pdata->usecase[idx].vectors[0].ib;
	}

	s = &a52_p427_bus_shadow[slot];
	WRITE_ONCE(s->handle, handle);
	WRITE_ONCE(s->curr, idx);
	WRITE_ONCE(s->num_paths, paths);
	WRITE_ONCE(s->ab, ab);
	WRITE_ONCE(s->ib, ib);
	smp_wmb();
	WRITE_ONCE(s->valid, 1U);
}

static void a52_p427_bus_shadow_clear(u32 handle,
				      struct msm_bus_client *client)
{
	int slot;

	if (!client || !client->pdata)
		return;
	slot = a52_p427_bus_slot(client->pdata->name);
	if (slot < 0)
		return;
	if (READ_ONCE(a52_p427_bus_shadow[slot].handle) == handle)
		WRITE_ONCE(a52_p427_bus_shadow[slot].valid, 0U);
}

void a52_p427_bus_get(u32 slot, u32 *valid, u32 *handle, s32 *curr,
			 u32 *num_paths, u64 *ab, u64 *ib)
{
	struct a52_p427_bus_shadow *s;

	if (slot >= A52_P427_BUS_SLOTS) {
		if (valid)
			*valid = 0;
		return;
	}
	s = &a52_p427_bus_shadow[slot];
	if (valid)
		*valid = READ_ONCE(s->valid);
	if (handle)
		*handle = READ_ONCE(s->handle);
	if (curr)
		*curr = READ_ONCE(s->curr);
	if (num_paths)
		*num_paths = READ_ONCE(s->num_paths);
	if (ab)
		*ab = READ_ONCE(s->ab);
	if (ib)
		*ib = READ_ONCE(s->ib);
}
EXPORT_SYMBOL_GPL(a52_p427_bus_get);
'''


def patch_bus_arb(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "static struct handle_type handle_list;",
        "static int update_request_adhoc(uint32_t cl, unsigned int index)",
        "static int update_context(uint32_t cl, bool active_only,",
        "static void unregister_client_adhoc(uint32_t cl)",
    ):
        if token not in text:
            raise SystemExit("Phase427 bus-arb prerequisite missing: " + token)

    text = one(text, "static struct handle_type handle_list;\n",
               "static struct handle_type handle_list;\n" + BUS_ARB_BLOCK + "\n",
               "bus shadow insertion")

    for sig, label in (
        ("static int update_request_adhoc(uint32_t cl, unsigned int index)", "update_request"),
        ("static int update_context(uint32_t cl, bool active_only,", "update_context"),
    ):
        a, b = function_bounds(text, sig)
        fn = text[a:b]
        if "struct msm_bus_client *client;" in fn:
            fn = fn.replace("struct msm_bus_client *client;",
                            "struct msm_bus_client *client = NULL;", 1)
        elif "struct msm_bus_client *client = NULL;" not in fn:
            raise SystemExit(f"Phase427 {label}: client declaration missing")
        exit_label = "exit_update_request:" if label == "update_request" else "exit_update_context:"
        if exit_label not in fn:
            raise SystemExit(f"Phase427 {label}: exit label missing")
        fn = fn.replace(exit_label,
                        "\tif (!ret && client)\n\t\ta52_p427_bus_shadow_update(cl, client);\n" + exit_label,
                        1)
        text = text[:a] + fn + text[b:]

    a, b = function_bounds(text, "static void unregister_client_adhoc(uint32_t cl)")
    fn = text[a:b]
    if "kfree(client);" not in fn:
        raise SystemExit("Phase427 unregister: kfree(client) missing")
    fn = fn.replace("\tkfree(client);",
                    "\ta52_p427_bus_shadow_clear(cl, client);\n\tkfree(client);", 1)
    text = text[:a] + fn + text[b:]
    text += "\n/* " + MARK + ": display msm_bus software shadows. */\n"
    return text


BUS_FAB_BLOCK = r'''
/* A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1
 * Shadow selected BCM aggregate values as they are about to be converted to
 * RPMh TCS commands.  F0 later reads the shadow only.
 */
#define A52_P427_BCM_SLOTS 7U
struct a52_p427_bcm_shadow {
	u32 valid;
	u32 addr;
	u64 vec_a;
	u64 vec_b;
};
static struct a52_p427_bcm_shadow a52_p427_bcm_shadow[A52_P427_BCM_SLOTS];

static int a52_p427_bcm_slot(const char *name)
{
	if (!name)
		return -1;
	if (!strcmp(name, "MM0")) return 0;
	if (!strcmp(name, "MM1")) return 1;
	if (!strcmp(name, "MM0_DISPLAY")) return 2;
	if (!strcmp(name, "MM1_DISPLAY")) return 3;
	if (!strcmp(name, "SH0")) return 4;
	if (!strcmp(name, "MC0")) return 5;
	if (!strcmp(name, "ACV")) return 6;
	return -1;
}

static void a52_p427_bcm_shadow_update(struct msm_bus_node_device_type *bcm)
{
	struct a52_p427_bcm_shadow *s;
	int slot;

	if (!bcm || !bcm->node_info || !bcm->bcmdev)
		return;
	slot = a52_p427_bcm_slot(bcm->node_info->name);
	if (slot < 0)
		return;
	s = &a52_p427_bcm_shadow[slot];
	WRITE_ONCE(s->addr, bcm->bcmdev->addr);
	WRITE_ONCE(s->vec_a, bcm->node_vec[ACTIVE_CTX].vec_a);
	WRITE_ONCE(s->vec_b, bcm->node_vec[ACTIVE_CTX].vec_b);
	smp_wmb();
	WRITE_ONCE(s->valid, 1U);
}

void a52_p427_bcm_get(u32 slot, u32 *valid, u32 *addr, u64 *vec_a, u64 *vec_b)
{
	struct a52_p427_bcm_shadow *s;

	if (slot >= A52_P427_BCM_SLOTS) {
		if (valid)
			*valid = 0;
		return;
	}
	s = &a52_p427_bcm_shadow[slot];
	if (valid) *valid = READ_ONCE(s->valid);
	if (addr) *addr = READ_ONCE(s->addr);
	if (vec_a) *vec_a = READ_ONCE(s->vec_a);
	if (vec_b) *vec_b = READ_ONCE(s->vec_b);
}
EXPORT_SYMBOL_GPL(a52_p427_bcm_get);
'''


def patch_bus_fab(text: str) -> str:
    if MARK in text:
        return text
    sig = "int msm_bus_commit_data(struct list_head *clist)"
    if sig not in text:
        raise SystemExit("Phase427 bus-fabric commit function missing")
    text = one(text, sig, BUS_FAB_BLOCK + "\n" + sig, "BCM shadow insertion")
