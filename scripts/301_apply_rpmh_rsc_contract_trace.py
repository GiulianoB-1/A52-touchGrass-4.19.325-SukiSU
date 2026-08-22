#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

SDE = Path('drivers/a52_display/msm/sde_rsc.c')
BUS = Path('drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c')
RSC = Path('drivers/soc/qcom/rpmh-rsc.c')
RPMH = Path('drivers/soc/qcom/rpmh.c')
COMPAT = Path('a52-port-compat.h')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1'
REC_INCLUDE = '#include <linux/a52_ack_secure_flight_recorder.h>\n'


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase301 {label}: expected exactly 1 anchor, found {count}')
    return text.replace(old, new, 1)


def ensure_include(text: str, include: str, anchor: str, label: str) -> str:
    if include in text:
        return text
    return one(text, anchor, anchor + include, f'{label} include')


def behavior_counts(text: str) -> dict[str, int]:
    tokens = (
        'rpmh_mode_solver_set(', 'rpmh_flush(', 'rpmh_invalidate(',
        'rpmh_write_batch(', 'rpmh_rsc_send_data(', 'rpmh_rsc_write_ctrl_data(',
        'writel(', 'writel_relaxed(', 'write_tcs_reg(', 'write_tcs_reg_sync(',
        '__tcs_buffer_write(', '__tcs_set_trigger(', 'msleep(', 'usleep_range(',
        'udelay(', 'wait_event_lock_irq(',
    )
    return {t: text.count(t) for t in tokens}


def patch_sde(text: str) -> str:
    if MARK in text:
        return text
    before = behavior_counts(text)
    text = ensure_include(text, REC_INCLUDE, '#include <linux/msm-bus.h>\n', 'sde recorder')
    text = ensure_include(text, '#include <linux/irqflags.h>\n', REC_INCLUDE, 'sde irqflags')
    text = one(
        text,
        REC_INCLUDE + '#include <linux/irqflags.h>\n',
        REC_INCLUDE + '#include <linux/irqflags.h>\n\n'
        '/* ' + MARK + '\n'
        ' * Observation only: the inherited Phase13 macros still erase\n'
        ' * rpmh_mode_solver_set and rpmh_flush. These markers record the\n'
        ' * exact runtime points at which Golden would execute those contracts.\n'
        ' */\n',
        'sde marker',
    )

    transitions = [
        (
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_CMD_STATE);\n',
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_CMD_STATE);\n'
            '\t\ta52_ackfr_record("P276 301S t=%d r=%d en=1 irq=%d",\n'
            '\t\t\tSDE_RSC_CMD_STATE, rc, irqs_disabled());\n',
            'CMD state update',
        ),
        (
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_CLK_STATE);\n',
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_CLK_STATE);\n'
            '\t\ta52_ackfr_record("P276 301S t=%d r=%d en=0 irq=%d",\n'
            '\t\t\tSDE_RSC_CLK_STATE, rc, irqs_disabled());\n',
            'CLK state update',
        ),
        (
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_VID_STATE);\n',
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_VID_STATE);\n'
            '\t\ta52_ackfr_record("P276 301S t=%d r=%d en=%d irq=%d",\n'
            '\t\t\tSDE_RSC_VID_STATE, rc,\n'
            '\t\t\trsc->version == SDE_RSC_REV_3, irqs_disabled());\n',
            'VID state update',
        ),
        (
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_IDLE_STATE);\n',
            '\t\trc = rsc->hw_ops.state_update(rsc, SDE_RSC_IDLE_STATE);\n'
            '\t\ta52_ackfr_record("P276 301S t=%d r=%d en=1 irq=%d",\n'
            '\t\t\tSDE_RSC_IDLE_STATE, rc, irqs_disabled());\n',
            'IDLE state update',
        ),
    ]
    for old, new, label in transitions:
        text = one(text, old, new, label)

    text = one(
        text,
        '\tif (delta_vote) {\n\t\tif (rsc->hw_ops.tcs_wait) {\n',
        '\tif (delta_vote) {\n'
        '\t\ta52_ackfr_record("P276 301V e cur=%d irq=%d",\n'
        '\t\t\trsc->current_state, irqs_disabled());\n'
        '\t\tif (rsc->hw_ops.tcs_wait) {\n',
        'vote entry',
    )
    text = one(
        text,
        '\t\t\trc = rsc->hw_ops.tcs_wait(rsc);\n\t\t\tif (rc) {\n',
        '\t\t\trc = rsc->hw_ops.tcs_wait(rsc);\n'
        '\t\t\ta52_ackfr_record("P276 301V tw=%d", rc);\n'
        '\t\t\tif (rc) {\n',
        'tcs wait result',
    )
    text = one(
        text,
        '\t\trpmh_invalidate(rsc->rpmh_dev);\n',
        '\t\ta52_ackfr_record("P276 301V inv dev=%s",\n'
        '\t\t\tdev_name(rsc->rpmh_dev));\n'
        '\t\trpmh_invalidate(rsc->rpmh_dev);\n',
        'sde invalidate',
    )
    text = one(
        text,
        '\t\trpmh_flush(rsc->rpmh_dev);\n',
        '\t\ta52_ackfr_record("P276 301F would dev=%s irq=%d",\n'
        '\t\t\tdev_name(rsc->rpmh_dev), irqs_disabled());\n'
        '\t\trpmh_flush(rsc->rpmh_dev);\n',
        'sde flush call',
    )
    after = behavior_counts(text)
    if before != after:
        raise SystemExit(f'Phase301 sde behavior-token counts changed: {before} -> {after}')
    return text


def patch_bus(text: str) -> str:
    if MARK in text:
        return text
    before = behavior_counts(text)
    text = ensure_include(text, REC_INCLUDE, '#include <soc/qcom/tcs.h>\n', 'bus recorder')
    text = one(
        text,
        REC_INCLUDE,
        REC_INCLUDE + '\n/* ' + MARK + ': Display-RSC bus votes, observation only. */\n',
        'bus marker',
    )

    text = one(
        text,
        '\tif (!cnt_active)\n\t\tgoto exit_msm_bus_commit_data;\n',
        '\tif (cur_rsc->node_info->id == MSM_BUS_RSC_DISP)\n'
        '\t\ta52_ackfr_record("P276 301B e ac=%d wk=%d sl=%d vcd=%d st=%d",\n'
        '\t\t\tcnt_active, cnt_wake, cnt_sleep, cnt_vcd,\n'
        '\t\t\tcur_rsc->rscdev->req_state);\n\n'
        '\tif (!cnt_active)\n\t\tgoto exit_msm_bus_commit_data;\n',
        'bus entry counts',
    )

    text = one(
        text,
        '\t/* rpmh_invalidate() is void in the pinned GKI 5.10 RPMh API. */\n\trpmh_invalidate(cur_mbox);\n',
        '\t/* rpmh_invalidate() is void in the pinned GKI 5.10 RPMh API. */\n'
        '\tif (cur_rsc->node_info->id == MSM_BUS_RSC_DISP)\n'
        '\t\ta52_ackfr_record("P276 301B inv mb=%s", dev_name(cur_mbox));\n'
        '\trpmh_invalidate(cur_mbox);\n',
        'bus invalidate',
    )

    active = '''\tif (cur_rsc->node_info->id == MSM_BUS_RSC_DISP) {
\t\tret = rpmh_write_batch(cur_mbox, cur_rsc->rscdev->req_state,
\t\t\t\t\t\tcmdlist_active, n_active);
'''
    active_new = '''\tif (cur_rsc->node_info->id == MSM_BUS_RSC_DISP) {
\t\tret = rpmh_write_batch(cur_mbox, cur_rsc->rscdev->req_state,
\t\t\t\t\t\tcmdlist_active, n_active);
\t\ta52_ackfr_record("P276 301B A r=%d st=%d mb=%s",
\t\t\tret, cur_rsc->rscdev->req_state, dev_name(cur_mbox));
'''
    text = one(text, active, active_new, 'display active batch')

    wake = '''\t\tret = rpmh_write_batch(cur_mbox, RPMH_WAKE_ONLY_STATE,
\t\t\t\t\t\t\tcmdlist_wake, n_wake);
\t\tif (ret)
'''
    wake_new = '''\t\tret = rpmh_write_batch(cur_mbox, RPMH_WAKE_ONLY_STATE,
\t\t\t\t\t\t\tcmdlist_wake, n_wake);
\t\tif (cur_rsc->node_info->id == MSM_BUS_RSC_DISP)
\t\t\ta52_ackfr_record("P276 301B W r=%d", ret);
\t\tif (ret)
'''
    text = one(text, wake, wake_new, 'wake batch')

    sleep = '''\t\tret = rpmh_write_batch(cur_mbox, RPMH_SLEEP_STATE,
\t\t\t\t\t\t\tcmdlist_sleep, n_sleep);
\t\tif (ret)
'''
    sleep_new = '''\t\tret = rpmh_write_batch(cur_mbox, RPMH_SLEEP_STATE,
\t\t\t\t\t\t\tcmdlist_sleep, n_sleep);
\t\tif (cur_rsc->node_info->id == MSM_BUS_RSC_DISP)
\t\t\ta52_ackfr_record("P276 301B S r=%d", ret);
\t\tif (ret)
'''
    text = one(text, sleep, sleep_new, 'sleep batch')

    after = behavior_counts(text)
    if before != after:
        raise SystemExit(f'Phase301 bus behavior-token counts changed: {before} -> {after}')
    return text


def patch_rsc(text: str) -> str:
    if MARK in text:
        return text
    before = behavior_counts(text)
    text = ensure_include(text, '#include <linux/string.h>\n', '#include <linux/spinlock.h>\n', 'rsc string')
    text = ensure_include(text, REC_INCLUDE, '#include <dt-bindings/soc/qcom,rpmh-rsc.h>\n', 'rsc recorder')
    anchor = '#include "trace-rpmh.h"\n'
    helper = '''#include "trace-rpmh.h"

/* A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1: observation only. */
static bool a52_p301_disp_rsc(const struct rsc_drv *drv)
{
\treturn drv && drv->name && !strcmp(drv->name, "disp_rsc");
}
'''
    text = one(text, anchor, helper, 'rsc helper')

    old_inv = '''void rpmh_rsc_invalidate(struct rsc_drv *drv)
{
\ttcs_invalidate(drv, SLEEP_TCS);
\ttcs_invalidate(drv, WAKE_TCS);
}
'''
    new_inv = '''void rpmh_rsc_invalidate(struct rsc_drv *drv)
{
\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301I s=%u w=%u use=%u",
\t\t\t(unsigned int)bitmap_weight(drv->tcs[SLEEP_TCS].slots, MAX_TCS_SLOTS),
\t\t\t(unsigned int)bitmap_weight(drv->tcs[WAKE_TCS].slots, MAX_TCS_SLOTS),
\t\t\t(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR));
\ttcs_invalidate(drv, SLEEP_TCS);
\ttcs_invalidate(drv, WAKE_TCS);
}
'''
    text = one(text, old_inv, new_inv, 'rsc invalidate trace')

    old_send = '''\ttcs = get_tcs_for_msg(drv, msg);
\tif (IS_ERR(tcs))
\t\treturn PTR_ERR(tcs);

\tspin_lock_irq(&drv->lock);
'''
    new_send = '''\ttcs = get_tcs_for_msg(drv, msg);
\tif (IS_ERR(tcs))
\t\treturn PTR_ERR(tcs);

\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d",
\t\t\tmsg->state, tcs->type, tcs->num_tcs, tcs->offset,
\t\t\t(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR),
\t\t\t(unsigned int)bitmap_weight(drv->tcs[WAKE_TCS].slots, MAX_TCS_SLOTS),
\t\t\tirqs_disabled());

\tspin_lock_irq(&drv->lock);
'''
    text = one(text, old_send, new_send, 'rsc send entry')

    old_claim = '''\ttcs->req[tcs_id - tcs->offset] = msg;
\tset_bit(tcs_id, drv->tcs_in_use);
'''
    new_claim = old_claim
    text = one(text, old_claim, new_claim, 'rsc send claim')

    old_trigger = '''\t__tcs_buffer_write(drv, tcs_id, 0, msg);
\t__tcs_set_trigger(drv, tcs_id, true);

\treturn 0;
}
'''
    new_trigger = '''\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301R c id=%d ty=%d", tcs_id, tcs->type);
\t__tcs_buffer_write(drv, tcs_id, 0, msg);
\t__tcs_set_trigger(drv, tcs_id, true);
\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301R x id=%d ty=%d", tcs_id, tcs->type);

\treturn 0;
}
'''
    text = one(text, old_trigger, new_trigger, 'rsc send exit')

    old_ctrl = '''\t/* find the TCS id and the command in the TCS to write to */
\tret = find_slots(tcs, msg, &tcs_id, &cmd_id);
\tif (!ret)
\t\t__tcs_buffer_write(drv, tcs_id, cmd_id, msg);

\treturn ret;
}
'''
    new_ctrl = '''\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301C e st=%d ty=%d n=%d slots=%u",
\t\t\tmsg->state, tcs->type, msg->num_cmds,
\t\t\t(unsigned int)bitmap_weight(tcs->slots, MAX_TCS_SLOTS));
\t/* find the TCS id and the command in the TCS to write to */
\tret = find_slots(tcs, msg, &tcs_id, &cmd_id);
\tif (!ret)
\t\t__tcs_buffer_write(drv, tcs_id, cmd_id, msg);
\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301C x r=%d id=%d cmd=%d slots=%u",
\t\t\tret, tcs_id, cmd_id,
\t\t\t(unsigned int)bitmap_weight(tcs->slots, MAX_TCS_SLOTS));

\treturn ret;
}
'''
    text = one(text, old_ctrl, new_ctrl, 'rsc ctrl trace')

    old_solver = '''\tsolver_config = readl_relaxed(base + DRV_SOLVER_CONFIG);
\tsolver_config &= DRV_HW_SOLVER_MASK << DRV_HW_SOLVER_SHIFT;
\tsolver_config = solver_config >> DRV_HW_SOLVER_SHIFT;
\tif (!solver_config) {
'''
    new_solver = '''\tsolver_config = readl_relaxed(base + DRV_SOLVER_CONFIG);
\tsolver_config &= DRV_HW_SOLVER_MASK << DRV_HW_SOLVER_SHIFT;
\tsolver_config = solver_config >> DRV_HW_SOLVER_SHIFT;
\tif (a52_p301_disp_rsc(drv))
\t\ta52_ackfr_record("P276 301P hw=%u a=%d sl=%d w=%d c=%d",
\t\t\tsolver_config, drv->tcs[ACTIVE_TCS].num_tcs,
\t\t\tdrv->tcs[SLEEP_TCS].num_tcs, drv->tcs[WAKE_TCS].num_tcs,
\t\t\tdrv->tcs[CONTROL_TCS].num_tcs);
\tif (!solver_config) {
'''
    text = one(text, old_solver, new_solver, 'rsc solver config')

    after = behavior_counts(text)
    if before != after:
        raise SystemExit(f'Phase301 rsc behavior-token counts changed: {before} -> {after}')
    return text


def check(root: Path) -> dict:
    for rel in (SDE, BUS, RSC, RPMH, COMPAT, REC):
        if not (root / rel).is_file():
            raise SystemExit(f'Phase301 missing {rel}')
    sde = (root / SDE).read_text(errors='replace')
    bus = (root / BUS).read_text(errors='replace')
    rsc = (root / RSC).read_text(errors='replace')
    compat = (root / COMPAT).read_text(errors='replace')
    markers = [
        'P276 301S t=%d r=%d en=1 irq=%d',
        'P276 301V e cur=%d irq=%d',
        'P276 301V tw=%d',
        'P276 301F would dev=%s irq=%d',
        'P276 301B e ac=%d wk=%d sl=%d vcd=%d st=%d',
        'P276 301B A r=%d st=%d mb=%s',
        'P276 301B W r=%d',
        'P276 301B S r=%d',
        'P276 301P hw=%u a=%d sl=%d w=%d c=%d',
        'P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d',
        'P276 301R c id=%d ty=%d',
        'P276 301C e st=%d ty=%d n=%d slots=%u',
        'P276 301I s=%u w=%u use=%u',
    ]
    joined = sde + bus + rsc
    missing = [m for m in markers if m not in joined]
    if missing:
        raise SystemExit('Phase301 missing markers: ' + repr(missing))
    required_compat = [
        '#define rpmh_mode_solver_set(d,e) do{}while(0)',
        '#define rpmh_flush(d) do{}while(0)',
        'A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS: diagnostic, non-flashable.',
    ]
    for token in required_compat:
        if token not in compat:
            raise SystemExit('Phase301 inherited Phase13 invariant missing: ' + token)
    if '#undef rpmh_mode_solver_set' in joined or '#undef rpmh_flush' in joined:
        raise SystemExit('Phase301 must not restore Phase13 RPMh behavior')
    return {
        'status': 'phase301-rpmh-rsc-contract-trace-v1-staged',
        'functional_change': 'instrumentation-only',
        'targets': [str(SDE), str(BUS), str(RSC)],
        'protected_unchanged': [str(RPMH), str(COMPAT), str(REC)],
        'phase13_solver_stub_preserved': True,
        'phase13_flush_stub_preserved': True,
        'marker_count': len(markers),
        'hardware_question': (
            'When SDE reaches its Golden solver/flush boundaries, does Phase296 send '
            'Display-RSC ACTIVE votes through the borrowed WAKE TCS and leave WAKE/SLEEP '
            'batches cached without the expected immediate flush?'
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--check-only', action='store_true')
    a = ap.parse_args()
    root = a.root.resolve()
    if not a.check_only:
        for rel, fn in ((SDE, patch_sde), (BUS, patch_bus), (RSC, patch_rsc)):
            p = root / rel
            if not p.is_file():
                raise SystemExit(f'Phase301 missing target {rel}')
            p.write_text(fn(p.read_text(errors='replace')))
    report = check(root)
    if a.report:
        a.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
