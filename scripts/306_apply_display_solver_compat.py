#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

RSC = Path('drivers/soc/qcom/rpmh-rsc.c')
COMPAT = Path('a52-port-compat.h')
MARK = 'A52_PHASE306_DISPLAY_SOLVER_COMPAT_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase306 {label}: expected exactly 1 anchor, found {n}')
    return text.replace(old, new, 1)


def patch_rsc(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1' not in text:
        raise SystemExit('Phase306 requires Phase305 flush repair')
    if 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' not in text:
        raise SystemExit('Phase306 requires inherited Phase301 observer')

    helper = '''static bool a52_p301_disp_rsc(const struct rsc_drv *drv)\n{\n\treturn drv && drv->name && !strcmp(drv->name, "disp_rsc");\n}\n'''
    helper_new = helper + '''\n/* A52_PHASE306_DISPLAY_SOLVER_COMPAT_V1: display-only solver ownership. */\nstatic bool a52_p306_disp_solver_owned;\n'''
    text = one(text, helper, helper_new, 'solver state insertion')

    flush_anchor = '''/*\n * A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1\n'''
    solver_fn = '''/*\n * Restore the Samsung display-facing rpmh_mode_solver_set() contract without\n * replacing the pinned 5.10 RPMh core. Golden waits for the dedicated ACTIVE\n * TCS, or the borrowed WAKE TCS when ACTIVE is absent, to become idle before\n * changing ownership. Phase305 already provides exactly that busy predicate.\n *\n * The state is display-scoped and serialized by drv->lock. While owned, the\n * ACTIVE send path below returns -EBUSY, which msm_bus_disp_rsc explicitly\n * treats as the expected solver-mode result.\n */\nint a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)\n{\n\tstruct rsc_drv *drv;\n\tunsigned long flags;\n\tbool old = false, waited = false;\n\tint ret = 0;\n\n\tif (!dev || !dev->parent) {\n\t\tret = -EINVAL;\n\t\tgoto out;\n\t}\n\n\tdrv = dev_get_drvdata(dev->parent);\n\tif (!drv) {\n\t\tret = -ENODEV;\n\t\tgoto out;\n\t}\n\n\t/* The compatibility contract is intentionally display-scoped. */\n\tif (!a52_p301_disp_rsc(drv))\n\t\tgoto out;\n\n\tfor (;;) {\n\t\tlocal_irq_save(flags);\n\t\tspin_lock(&drv->lock);\n\t\tif (!rpmh_rsc_ctrlr_is_busy(drv))\n\t\t\tbreak;\n\t\tspin_unlock(&drv->lock);\n\t\tlocal_irq_restore(flags);\n\t\twaited = true;\n\t\tcpu_relax();\n\t}\n\n\told = a52_p306_disp_solver_owned;\n\ta52_p306_disp_solver_owned = enable;\n\tspin_unlock(&drv->lock);\n\tlocal_irq_restore(flags);\n\nout:\n\ta52_ackfr_record("P276 306M e=%u o=%u w=%u r=%d",\n\t\tenable, old, waited, ret);\n\treturn ret;\n}\n\n'''
    text = one(text, flush_anchor, solver_fn + flush_anchor, 'solver compatibility function insertion')

    gate_anchor = '''\tspin_lock_irq(&drv->lock);\n\n\t/* Wait forever for a free tcs. It better be there eventually! */\n'''
    gate_new = '''\tspin_lock_irq(&drv->lock);\n\n\t/*\n\t * Golden rejects ACTIVE/AMC traffic while disp_rsc is solver-owned.\n\t * Without this Phase13 semantic, pinned 5.10 borrows WAKE_TCS and\n\t * triggers it as an ACTIVE transaction.\n\t */\n\tif (a52_p301_disp_rsc(drv) &&\n\t    msg->state == RPMH_ACTIVE_ONLY_STATE &&\n\t    a52_p306_disp_solver_owned) {\n\t\tspin_unlock_irq(&drv->lock);\n\t\ta52_ackfr_record("P276 306G b st=%d ty=%d",\n\t\t\tmsg->state, tcs->type);\n\t\treturn -EBUSY;\n\t}\n\n\t/* Wait forever for a free tcs. It better be there eventually! */\n'''
    return one(text, gate_anchor, gate_new, 'ACTIVE solver gate insertion')


def patch_compat(text: str) -> str:
    if MARK in text:
        return text
    old = '''#define rpmh_mode_solver_set(d,e) do{}while(0)\n/* A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1: flush-only causal repair. */\nint a52_rpmh_flush_compat(const struct device *dev);\n#define rpmh_flush(d) a52_rpmh_flush_compat((d))\n'''
    new = '''/* A52_PHASE306_DISPLAY_SOLVER_COMPAT_V1: restore display solver ownership. */\nint a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable);\n#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))\n/* A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1: retained flush repair. */\nint a52_rpmh_flush_compat(const struct device *dev);\n#define rpmh_flush(d) a52_rpmh_flush_compat((d))\n'''
    return one(text, old, new, 'Phase13 solver stub replacement')


def validate(rsc: str, compat: str) -> None:
    joined = rsc + compat
    required = [
        MARK,
        'static bool a52_p306_disp_solver_owned;',
        'int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)',
        'rpmh_rsc_ctrlr_is_busy(drv)',
        'cpu_relax();',
        'a52_p306_disp_solver_owned = enable;',
        'P276 306M e=%u o=%u w=%u r=%d',
        'msg->state == RPMH_ACTIVE_ONLY_STATE',
        'P276 306G b st=%d ty=%d',
        'return -EBUSY;',
        '#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))',
        '#define rpmh_flush(d) a52_rpmh_flush_compat((d))',
        'P276 305F e',
        'P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d',
    ]
    for token in required:
        if token not in joined:
            raise SystemExit('Phase306 required token missing: ' + token)
    if '#define rpmh_mode_solver_set(d,e) do{}while(0)' in compat:
        raise SystemExit('Phase306 legacy solver erasure still present')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    rp = args.root / RSC
    cp = args.root / COMPAT
    if not rp.is_file() or not cp.is_file():
        raise SystemExit('Phase306 required source files missing')
    if not args.check_only:
        rp.write_text(patch_rsc(rp.read_text()))
        cp.write_text(patch_compat(cp.read_text()))
    validate(rp.read_text(), cp.read_text())
    print('Phase306 display solver ownership compatibility repair: PASS')


if __name__ == '__main__':
    main()
