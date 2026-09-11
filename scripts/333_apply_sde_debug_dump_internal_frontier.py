#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

SDE = Path('drivers/a52_display/msm/sde_dbg.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE333_SDE_DEBUG_DUMP_INTERNAL_FRONTIER_V1'

REC_ADMIT_OLD = """\t      fmt[6] == '3' && (fmt[7] == '1' || fmt[7] == '2')))
"""
REC_ADMIT_NEW = """\t      fmt[6] == '3' &&
\t      (fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '3')))
"""

REC_EXPORT_OLD = """void a52_ackfr_retain_timeout_snapshot(void)
{
\tatomic_set(&a52_r280_retained, 1);
}
EXPORT_SYMBOL_GPL(a52_ackfr_retain_timeout_snapshot);

"""
REC_EXPORT_NEW = """void a52_ackfr_retain_timeout_snapshot(void)
{
\tatomic_set(&a52_r280_retained, 1);
}
EXPORT_SYMBOL_GPL(a52_ackfr_retain_timeout_snapshot);

/* A52_PHASE333_SDE_DEBUG_DUMP_INTERNAL_FRONTIER_V1 */
bool a52_ackfr_timeout_retained(void)
{
\treturn unlikely(atomic_read(&a52_r280_retained));
}
EXPORT_SYMBOL_GPL(a52_ackfr_timeout_retained);

"""

SDE_SIG = """static void _sde_dump_array(struct sde_dbg_reg_base *blk_arr[],
\tu32 len, bool do_panic, const char *name, bool dump_dbgbus_sde,
\tbool dump_dbgbus_vbif_rt, bool dump_all, bool dump_secure)
{
\tint i;
\tu32 reg_dump_size;
\tstruct sde_dbg_base *dbg_base = &sde_dbg_base;

\tmutex_lock(&sde_dbg_base.mutex);

\treg_dump_size =  _sde_dbg_get_reg_dump_size();
"""
SDE_SIG_NEW = """/* A52_PHASE333_SDE_DEBUG_DUMP_INTERNAL_FRONTIER_V1
 * Persistent-recorder-only breadcrumbs inside the synchronous SDE failure
 * dump. No dump selection, MMIO, power, lock, panic or return behavior changes.
 */
extern void a52_ackfr_record(const char *fmt, ...);
extern bool a52_ackfr_timeout_retained(void);

static void _sde_dump_array(struct sde_dbg_reg_base *blk_arr[],
\tu32 len, bool do_panic, const char *name, bool dump_dbgbus_sde,
\tbool dump_dbgbus_vbif_rt, bool dump_all, bool dump_secure)
{
\tint i;
\tu32 reg_dump_size;
\tstruct sde_dbg_base *dbg_base = &sde_dbg_base;

\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333A p=%u a=%u s=%u v=%u",
\t\t\t(unsigned int)do_panic, (unsigned int)dump_all,
\t\t\t(unsigned int)dump_dbgbus_sde,
\t\t\t(unsigned int)dump_dbgbus_vbif_rt);

\tmutex_lock(&sde_dbg_base.mutex);
\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333B lock=1");

\treg_dump_size =  _sde_dbg_get_reg_dump_size();
"""

MEM_OLD = """\tif (!dbg_base->reg_dump_addr)
\t\tpr_err("Failed to allocate memory for reg_dump_addr size:%d\\n",
\t\t\t\treg_dump_size);

\tif (dump_all)
\t\tsde_evtlog_dump_all(sde_dbg_base.evtlog);

\tif (dump_all || !blk_arr || !len) {
"""
MEM_NEW = """\tif (!dbg_base->reg_dump_addr)
\t\tpr_err("Failed to allocate memory for reg_dump_addr size:%d\\n",
\t\t\t\treg_dump_size);
\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333C mem=%u sz=%u",
\t\t\t(unsigned int)!!dbg_base->reg_dump_addr, reg_dump_size);

\tif (dump_all) {
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333D evt=0");
\t\tsde_evtlog_dump_all(sde_dbg_base.evtlog);
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333E evt=1");
\t}

\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333F reg=0");
\tif (dump_all || !blk_arr || !len) {
"""

REG_END_OLD = """\t\t}
\t}

\tif (dump_dbgbus_sde)
\t\t_sde_dbg_dump_sde_dbg_bus(&sde_dbg_base.dbgbus_sde);

\tif (dump_dbgbus_vbif_rt)
\t\t_sde_dbg_dump_vbif_dbg_bus(&sde_dbg_base.dbgbus_vbif_rt);

\tif (sde_dbg_base.dsi_dbg_bus || dump_all)
\t\tdsi_ctrl_debug_dump(sde_dbg_base.dbgbus_dsi.entries,
\t\t\t\t    sde_dbg_base.dbgbus_dsi.size);

#if defined(CONFIG_DISPLAY_SAMSUNG)
\tif (do_panic && sde_dbg_base.panic_on_err)
\t\tss_store_xlog_panic_dbg();
#endif

\tif (do_panic && sde_dbg_base.panic_on_err)
\t\tpanic(name);

\tmutex_unlock(&sde_dbg_base.mutex);
}
"""
REG_END_NEW = """\t\t}
\t}
\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333G reg=1");

\tif (dump_dbgbus_sde) {
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333H sde=0");
\t\t_sde_dbg_dump_sde_dbg_bus(&sde_dbg_base.dbgbus_sde);
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333I sde=1");
\t}

\tif (dump_dbgbus_vbif_rt) {
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333J vbif=0");
\t\t_sde_dbg_dump_vbif_dbg_bus(&sde_dbg_base.dbgbus_vbif_rt);
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333K vbif=1");
\t}

\tif (sde_dbg_base.dsi_dbg_bus || dump_all) {
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333L dsi=0");
\t\tdsi_ctrl_debug_dump(sde_dbg_base.dbgbus_dsi.entries,
\t\t\t\t    sde_dbg_base.dbgbus_dsi.size);
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333M dsi=1");
\t}

#if defined(CONFIG_DISPLAY_SAMSUNG)
\tif (do_panic && sde_dbg_base.panic_on_err) {
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333N xlog=0");
\t\tss_store_xlog_panic_dbg();
\t\tif (a52_ackfr_timeout_retained())
\t\t\ta52_ackfr_record("P276 333O xlog=1");
\t}
#endif

\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333P panic=%u pe=%u",
\t\t\t(unsigned int)do_panic,
\t\t\t(unsigned int)sde_dbg_base.panic_on_err);
\tif (do_panic && sde_dbg_base.panic_on_err)
\t\tpanic(name);
\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333Q panic=return");

\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333R unlock=0");
\tmutex_unlock(&sde_dbg_base.mutex);
\tif (a52_ackfr_timeout_retained())
\t\ta52_ackfr_record("P276 333S unlock=1");
}
"""

def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        raise SystemExit(f'Phase333 {label}: expected 1 match, found {n}')
    return s.replace(old, new, 1)

def patch_sde(s: str) -> str:
    if MARK in s:
        return s
    s = one(s, SDE_SIG, SDE_SIG_NEW, 'dump entry')
    s = one(s, MEM_OLD, MEM_NEW, 'allocation/event/register frontier')
    s = one(s, REG_END_OLD, REG_END_NEW, 'debug-bus/panic frontier')
    return s

def patch_rec(s: str) -> str:
    if MARK in s:
        return s
    if 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' not in s:
        raise SystemExit('Phase333 recorder requires Phase332')
    s = one(s, REC_ADMIT_OLD, REC_ADMIT_NEW, 'post-retention namespace')
    s = one(s, REC_EXPORT_OLD, REC_EXPORT_NEW, 'retained-state query')
    return s

def validate(sde: str, rec: str) -> None:
    for x in (
        MARK,
        'P276 333A p=%u a=%u s=%u v=%u',
        'P276 333B lock=1',
        'P276 333C mem=%u sz=%u',
        'P276 333D evt=0', 'P276 333E evt=1',
        'P276 333F reg=0', 'P276 333G reg=1',
        'P276 333H sde=0', 'P276 333I sde=1',
        'P276 333J vbif=0', 'P276 333K vbif=1',
        'P276 333L dsi=0', 'P276 333M dsi=1',
        'P276 333N xlog=0', 'P276 333O xlog=1',
        'P276 333P panic=%u pe=%u', 'P276 333Q panic=return',
        'P276 333R unlock=0', 'P276 333S unlock=1',
    ):
        if x not in sde:
            raise SystemExit('Phase333 SDE token missing: ' + x)
    for x in (
        MARK,
        'a52_ackfr_timeout_retained(void)',
        'EXPORT_SYMBOL_GPL(a52_ackfr_timeout_retained);',
        "fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '3'",
    ):
        if x not in rec:
            raise SystemExit('Phase333 recorder token missing: ' + x)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    sp, rp = ns.root / SDE, ns.root / REC
    if not sp.is_file() or not rp.is_file():
        raise SystemExit('Phase333 source missing')
    if not ns.check_only:
        sp.write_text(patch_sde(sp.read_text()))
        rp.write_text(patch_rec(rp.read_text()))
    validate(sp.read_text(), rp.read_text())
    print('Phase333 SDE debug-dump internal frontier recorder: PASS')

if __name__ == '__main__':
    main()
