#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

RSC = Path('drivers/soc/qcom/rpmh-rsc.c')
COMPAT = Path('a52-port-compat.h')
MARK = 'A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase305 {label}: expected exactly 1 anchor, found {count}')
    return text.replace(old, new, 1)


def patch_rsc(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' not in text:
        raise SystemExit('Phase305 requires inherited Phase301 RPMh/RSC observer')
    if 'P276 301I s=%u w=%u use=%u' not in text:
        raise SystemExit('Phase305 requires inherited low-level invalidate observer')

    text = one(
        text,
        '#include <linux/interrupt.h>\n',
        '#include <linux/interrupt.h>\n#include <linux/irqflags.h>\n',
        'irqflags include',
    )

    anchor = '''static bool rpmh_rsc_ctrlr_is_busy(struct rsc_drv *drv)\n{\n\tint m;\n\tstruct tcs_group *tcs = &drv->tcs[ACTIVE_TCS];\n\n\t/*\n\t * If we made an active request on a RSC that does not have a\n\t * dedicated TCS for active state use, then re-purposed wake TCSes\n\t * should be checked for not busy, because we used wake TCSes for\n\t * active requests in this case.\n\t */\n\tif (!tcs->num_tcs)\n\t\ttcs = &drv->tcs[WAKE_TCS];\n\n\tfor (m = tcs->offset; m < tcs->offset + tcs->num_tcs; m++) {\n\t\tif (!tcs_is_free(drv, m))\n\t\t\treturn true;\n\t}\n\n\treturn false;\n}\n'''

    wrapper = anchor + '''\n/*\n * A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1\n *\n * Samsung 4.19 SDE calls rpmh_flush(struct device *), but the pinned 5.10\n * core owns rpmh_flush(struct rpmh_ctrlr *). Phase13 erased the SDE call.\n * Hardware Phase304 proved that the erased call is reached immediately\n * before the exact F0 5A 5A DSI DMA timeout. Bridge only that legacy display\n * call to the native 5.10 flush contract. Solver-mode behavior remains\n * untouched in this phase.\n *\n * Match the native 5.10 CPU-PM exclusion contract: disable local IRQs,\n * try drv->lock, reject while an ACTIVE transfer is in flight, then call\n * native rpmh_flush(), whose cache_lock nests inside drv->lock.\n */\nint a52_rpmh_flush_compat(const struct device *dev)\n{\n\tstruct rsc_drv *drv;\n\tunsigned long irq_flags;\n\tbool locked = false, busy = false;\n\tint ret;\n\n\ta52_ackfr_record("P276 305F e");\n\n\tif (!dev || !dev->parent) {\n\t\tret = -EINVAL;\n\t\tgoto out_record;\n\t}\n\n\tdrv = dev_get_drvdata(dev->parent);\n\tif (!drv) {\n\t\tret = -ENODEV;\n\t\tgoto out_record;\n\t}\n\n\tlocal_irq_save(irq_flags);\n\tif (!spin_trylock(&drv->lock)) {\n\t\tret = -EBUSY;\n\t\tgoto out_irq;\n\t}\n\tlocked = true;\n\n\tbusy = rpmh_rsc_ctrlr_is_busy(drv);\n\tif (busy)\n\t\tret = -EBUSY;\n\telse\n\t\tret = rpmh_flush(&drv->client);\n\n\tspin_unlock(&drv->lock);\nout_irq:\n\tlocal_irq_restore(irq_flags);\nout_record:\n\ta52_ackfr_record("P276 305F x r=%d l=%u b=%u", ret, locked, busy);\n\treturn ret;\n}\n'''
    return one(text, anchor, wrapper, 'compat wrapper insertion')


def patch_compat(text: str) -> str:
    if MARK in text:
        return text
    old = '#define rpmh_mode_solver_set(d,e) do{}while(0)\n#define rpmh_flush(d) do{}while(0)\n'
    new = '''#define rpmh_mode_solver_set(d,e) do{}while(0)\n/* A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1: flush-only causal repair. */\nint a52_rpmh_flush_compat(const struct device *dev);\n#define rpmh_flush(d) a52_rpmh_flush_compat((d))\n'''
    return one(text, old, new, 'Phase13 flush stub replacement')


def validate(rsc: str, compat: str) -> None:
    joined = rsc + compat
    required = [
        MARK,
        'int a52_rpmh_flush_compat(const struct device *dev)',
        'local_irq_save(irq_flags);',
        'spin_trylock(&drv->lock)',
        'rpmh_rsc_ctrlr_is_busy(drv)',
        'rpmh_flush(&drv->client)',
        'local_irq_restore(irq_flags);',
        'P276 305F e',
        'P276 305F x r=%d l=%u b=%u',
        '#define rpmh_mode_solver_set(d,e) do{}while(0)',
        '#define rpmh_flush(d) a52_rpmh_flush_compat((d))',
        'P276 301I s=%u w=%u use=%u',
    ]
    for token in required:
        if token not in joined:
            raise SystemExit('Phase305 required token missing: ' + token)
    if '#define rpmh_flush(d) do{}while(0)' in compat:
        raise SystemExit('Phase305 legacy flush erasure still present')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    rp = args.root / RSC
    cp = args.root / COMPAT
    if not rp.is_file() or not cp.is_file():
        raise SystemExit('Phase305 required source files missing')

    if not args.check_only:
        rp.write_text(patch_rsc(rp.read_text()))
        cp.write_text(patch_compat(cp.read_text()))

    validate(rp.read_text(), cp.read_text())
    print('Phase305 display RPMh flush compatibility repair: PASS')


if __name__ == '__main__':
    main()
