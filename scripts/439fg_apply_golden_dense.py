#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE439FG_GOLDEN_DENSE_CLOCK_V1"

HWC_CANDIDATES = (
    Path("dsi_ctrl_hw_cmn.c"),
    Path("techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c"),
    Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"),
)
CTRL_CANDIDATES = (
    Path("dsi_ctrl.c"),
    Path("techpack/display/msm/dsi/dsi_ctrl.c"),
    Path("drivers/a52_display/msm/dsi/dsi_ctrl.c"),
)

def resolve_source(root: Path, candidates: tuple[Path, ...], label: str) -> Path:
    matches = [root / rel for rel in candidates if (root / rel).is_file()]
    if len(matches) != 1:
        die(f"{label}: expected exactly one source under {root}, found {len(matches)}: " +
            ", ".join(str(p) for p in matches))
    return matches[0]

def die(msg: str) -> None:
    raise SystemExit("Phase439FG: " + msg)

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

HELPER = r'''
/* A52_PHASE439FG_GOLDEN_DENSE_CLOCK_V1
 * Exact Golden twin of Phase439F. The ~1 us hot loop reads only DSI-local
 * registers. disp_cc provider state is read only before trigger, after the
 * 64 us observation window, and after normal DMA completion.
 */
#define A52_G439F_DENSE_MAX 64U
#define A52_G439F_PROVIDER_MAX 4U

struct a52_g439f_dense_sample {
	u64 ns;
	u32 idx, status, clk_status, int_ctrl, dbg171;
};

struct a52_g439f_provider_sample {
	u64 ns;
	u32 point;
	u32 status, clk_ctrl, clk_status, dbg171;
	u32 pclk0_cbcr, byte0_cbcr, byte0_intf_cbcr, esc0_cbcr;
	u32 pclk0_cmd, pclk0_cfg, byte0_cmd, byte0_cfg;
	u32 esc0_cmd, esc0_cfg, pll_status;
};

static struct a52_g439f_dense_sample a52_g439f_dense[A52_G439F_DENSE_MAX];
static struct a52_g439f_provider_sample a52_g439f_provider[A52_G439F_PROVIDER_MAX];
static atomic_t a52_g439f_dense_count = ATOMIC_INIT(0);
static atomic_t a52_g439f_provider_count = ATOMIC_INIT(0);
static atomic_t a52_g439f_terminal_once = ATOMIC_INIT(0);
static u64 a52_g439f_trigger_ns;

static __always_inline u32 a52_g439f_disp_r(struct dsi_ctrl_hw *ctrl, u32 off)
{
	return (ctrl && ctrl->disp_cc_base) ? DSI_DISP_CC_R32(ctrl, off) : ~0U;
}

static void a52_g439f_provider_capture(struct dsi_ctrl_hw *ctrl, u32 point)
{
	struct a52_g439f_provider_sample *s;
	unsigned int i;

	if (!ctrl || !ctrl->base)
		return;

	i = (unsigned int)atomic_inc_return(&a52_g439f_provider_count) - 1U;
	if (i >= A52_G439F_PROVIDER_MAX)
		return;

	s = &a52_g439f_provider[i];
	memset(s, 0, sizeof(*s));
	s->ns = ktime_get_ns();
	s->point = point;
	s->status = DSI_R32(ctrl, DSI_STATUS);
	s->clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	s->pclk0_cbcr = a52_g439f_disp_r(ctrl, 0x100c);
	s->byte0_cbcr = a52_g439f_disp_r(ctrl, 0x102c);
	s->byte0_intf_cbcr = a52_g439f_disp_r(ctrl, 0x1030);
	s->esc0_cbcr = a52_g439f_disp_r(ctrl, 0x1034);
	s->pclk0_cmd = a52_g439f_disp_r(ctrl, 0x1064);
	s->pclk0_cfg = a52_g439f_disp_r(ctrl, 0x1068);
	s->byte0_cmd = a52_g439f_disp_r(ctrl, 0x10c4);
	s->byte0_cfg = a52_g439f_disp_r(ctrl, 0x10c8);
	s->esc0_cmd = a52_g439f_disp_r(ctrl, 0x10e0);
	s->esc0_cfg = a52_g439f_disp_r(ctrl, 0x10e4);
	s->pll_status = a52_g439_pr(0xba0);
}

static __always_inline void a52_g439f_note_trigger(void)
{
	WRITE_ONCE(a52_g439f_trigger_ns, ktime_get_ns());
}

static void a52_g439f_dense_run(struct dsi_ctrl_hw *ctrl)
{
	u64 start = READ_ONCE(a52_g439f_trigger_ns);
	unsigned int i;

	if (!ctrl || !ctrl->base || !start)
		return;

	atomic_set(&a52_g439f_dense_count, 0);
	for (i = 0; i < A52_G439F_DENSE_MAX; i++) {
		struct a52_g439f_dense_sample *s = &a52_g439f_dense[i];
		u64 target = start + (u64)i * 1000ULL;

		while (ktime_get_ns() < target)
			cpu_relax();

		s->ns = ktime_get_ns();
		s->idx = i;
		s->status = DSI_R32(ctrl, DSI_STATUS);
		s->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
		s->int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
		s->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
		atomic_set(&a52_g439f_dense_count, i + 1U);
	}

	a52_g439f_provider_capture(ctrl, 60U);
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_g439_saved_dbg);
	wmb();
}

void a52_g439f_terminal(struct dsi_ctrl_hw *ctrl, u32 point)
{
	if (atomic_cmpxchg(&a52_g439f_terminal_once, 0, 1) != 0)
		return;
	a52_g439f_provider_capture(ctrl, point);
}
'''

SEQ = r'''
static void a52_g439f_seq_dump(struct seq_file *m)
{
	unsigned int i, n;

	seq_printf(m, "TG439F trigger_ns=%llu dense=%d providers=%d\n",
		(unsigned long long)a52_g439f_trigger_ns,
		atomic_read(&a52_g439f_dense_count),
		atomic_read(&a52_g439f_provider_count));

	n = (unsigned int)atomic_read(&a52_g439f_provider_count);
	if (n > A52_G439F_PROVIDER_MAX)
		n = A52_G439F_PROVIDER_MAX;

	for (i = 0; i < n; i++) {
		const struct a52_g439f_provider_sample *s = &a52_g439f_provider[i];

		seq_printf(m,
			"TG439F V i=%u p=%u ns=%llu st=%08x cc=%08x ck=%08x db=%08x pc=%08x bc=%08x bi=%08x ec=%08x pm=%08x pf=%08x bm=%08x bf=%08x em=%08x ef=%08x ps=%08x\n",
			i, s->point, (unsigned long long)s->ns,
			s->status, s->clk_ctrl, s->clk_status, s->dbg171,
			s->pclk0_cbcr, s->byte0_cbcr, s->byte0_intf_cbcr,
			s->esc0_cbcr, s->pclk0_cmd, s->pclk0_cfg,
			s->byte0_cmd, s->byte0_cfg, s->esc0_cmd, s->esc0_cfg,
			s->pll_status);
	}

	n = (unsigned int)atomic_read(&a52_g439f_dense_count);
	if (n > A52_G439F_DENSE_MAX)
		n = A52_G439F_DENSE_MAX;

	for (i = 0; i < n; i++) {
		const struct a52_g439f_dense_sample *s = &a52_g439f_dense[i];

		seq_printf(m,
			"TG439F F i=%u ns=%llu delta_ns=%llu st=%08x ck=%08x in=%08x db=%08x\n",
			i, (unsigned long long)s->ns,
			(unsigned long long)(s->ns - a52_g439f_trigger_ns),
			s->status, s->clk_status, s->int_ctrl, s->dbg171);
	}
}
'''

def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2" not in text:
        die("Phase439G marker missing")

    anchor = "static void a52_g439_debug_sweep(struct dsi_ctrl_hw *ctrl)\n"
    text = one(text, anchor, HELPER + "\n" + anchor, "helper")

    seq_anchor = "static void a52_g439_seq_dump(struct seq_file *m)\n"
    text = one(text, seq_anchor, SEQ + "\n" + seq_anchor, "seq helper")

    old = '''\t}
}

static int a52_g439_proc_show'''
    new = '''\t}
\ta52_g439f_seq_dump(m);
}

static int a52_g439_proc_show'''
    text = one(text, old, new, "proc dump hook")

    old = '''\t\ta52_g439_begin(ctrl);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\ta52_g439_post_trigger(ctrl);'''
    new = '''\t\ta52_g439_begin(ctrl);
\t\ta52_g439f_provider_capture(ctrl, 0U);
\t\ta52_g439f_note_trigger();
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\ta52_g439f_dense_run(ctrl);'''
    text = one(text, old, new, "memory trigger")

    old = '''\ta52_g439_begin(ctrl);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\ta52_g439_post_trigger(ctrl);'''
    new = '''\ta52_g439_begin(ctrl);
\ta52_g439f_provider_capture(ctrl, 0U);
\ta52_g439f_note_trigger();
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\ta52_g439f_dense_run(ctrl);'''
    text = one(text, old, new, "deferred trigger")

    return text + (
        f'\nstatic const char a52_g439f_marker[] __used = "{MARK}";\n'
    )

def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2" not in text:
        die("Phase439G CTRL marker missing")

    decl = "extern void a52_g439_dump_samples(struct dsi_ctrl_hw *ctrl);\n"
    text = one(
        text, decl,
        decl + "extern void a52_g439f_terminal(struct dsi_ctrl_hw *ctrl, u32 point);\n",
        "terminal declaration",
    )

    old = '''\tif (a52_g315_armed(dsi_ctrl)) {
\t\ta52_g344_snapshot(&dsi_ctrl->hw, 9);'''
    new = '''\tif (a52_g315_armed(dsi_ctrl)) {
\t\ta52_g439f_terminal(&dsi_ctrl->hw, ret ? 300U : 200U);
\t\ta52_g344_snapshot(&dsi_ctrl->hw, 9);'''
    text = one(text, old, new, "terminal capture")

    return text + f'\n/* {MARK}: terminal provider capture. */\n'

def validate(root: Path) -> None:
    hp = resolve_source(root, HWC_CANDIDATES, "HWC")
    cp = resolve_source(root, CTRL_CANDIDATES, "CTRL")
    text = hp.read_text(errors="replace") + "\n" + cp.read_text(errors="replace")

    for token in (
        MARK, "a52_g439f_dense_run", "a52_g439f_terminal",
        "TG439F F i=%u", "0x100c", "0x1064", "0x10c4", "0x10e0",
    ):
        if token not in text:
            die("missing " + token)

    if text.count("a52_g439_post_trigger(ctrl);") != 0:
        die("old Golden post-trigger helper still called")

    print("Phase439F Golden dense clock observer: PASS")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()

    if not ns.check_only:
        hp = resolve_source(root, HWC_CANDIDATES, "HWC")
        cp = resolve_source(root, CTRL_CANDIDATES, "CTRL")
        hp.write_text(patch_hwc(hp.read_text(errors="replace")))
        cp.write_text(patch_ctrl(cp.read_text(errors="replace")))

    validate(root)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
