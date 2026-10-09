#!/usr/bin/env python3
"""Phase446k: single-variable GKI cleanup-to-first-panel-command delay.

Apply after the Phase446j instrumentation. This only changes the GKI continuous
splash path; does not touch DSI clocks, PHY, registers, panel ID or TE routing.
"""
import argparse
from pathlib import Path

MARK = 'A52_PHASE446K_CLEANUP_TO_F0_DELAY_35MS_V1'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    a = ap.parse_args()
    path = a.root / 'drivers/a52_display/msm/dsi/dsi_display.c'
    source = path.read_text()
    if not a.check_only and MARK not in source:
        if source.count('#include <linux/jiffies.h>') != 1:
            raise RuntimeError('cannot find original includes')
        source = source.replace('#include <linux/jiffies.h>',
                                '#include <linux/jiffies.h>\n#include <linux/delay.h>', 1)
        start = source.index('int dsi_display_enable(struct dsi_display *display)')
        end = source.find('\nint dsi_display_disable(', start)
        if end == -1:
            end = source.find('\nstatic int dsi_display_disable(', start)
        if end == -1:
            raise RuntimeError('cannot find end of enable function')
        body = source[start:end]
        anchor = ('\t\trc = dsi_display_splash_res_cleanup(display);\n'
                  '\t\tif (rc) {\n'
                  '\t\t\tDSI_ERR("Continuous splash res cleanup failed, rc=%d\\n",\n'
                  '\t\t\t\trc);\n'
                  '\t\t\treturn -EINVAL;\n'
                  '\t\t}\n')
        if body.count(anchor) != 1:
            raise RuntimeError(f'unexpected cleanup anchor count={body.count(anchor)}')
        additional = (
            '\n\t\t/* ' + MARK + '\n'
            '\t\t * One-shot, sleepable, timing-only A/B: match Golden\'s\n'
            '\t\t * ~33 ms cleanup-to-first-F0 corridor. No register writes.\n'
            '\t\t */\n'
            '\t\tmsleep(35);\n')
        body = body.replace(anchor, anchor + additional, 1)
        if body.find('msleep(35)') > body.find('rc = dsi_panel_enable(display->panel)'):
            raise RuntimeError('delay is after the first panel enable')
        source = source[:start] + body + source[end:]
        path.write_text(source)
    content = path.read_text()
    assert content.count(MARK) == 1, 'missing or duplicated delay'
    assert content.count('msleep(35);') == 1, 'delay count mismatch'
    assert '#include <linux/delay.h>' in content
    print('Phase446k delay 35ms after successful splash cleanup: PASS')


if __name__ == '__main__':
    main()
