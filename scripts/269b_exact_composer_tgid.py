#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1])
recp = root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c'
msmp = root / 'drivers/a52_display/msm/msm_drv.c'


def one(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


rec = recp.read_text(encoding='utf-8')
old = '''static bool a52_r268_is_composer_pid(int pid)\n{\n\treturn pid >= 0 && pid == atomic_read(&a52_r268_comp_pid);\n}\n'''
new = '''static bool a52_r268_is_composer_pid(int pid)\n{\n\treturn pid >= 0 && pid == atomic_read(&a52_r268_comp_pid);\n}\n\n/* A52_PHASE269_EXACT_COMPOSER_TGID_V1\n * Reuse Phase268's exec-derived composer identity. This avoids task-comm\n * ambiguity (vendor.qti.hardware.* truncates to vendor.qti.hard).\n */\nbool a52_ackfr_phase269_is_composer_tgid(pid_t tgid)\n{\n\treturn a52_r268_is_composer_pid((int)tgid);\n}\nEXPORT_SYMBOL_GPL(a52_ackfr_phase269_is_composer_tgid);\n'''
rec = one(rec, old, new, 'export exact composer TGID helper')
recp.write_text(rec, encoding='utf-8')

msm = msmp.read_text(encoding='utf-8')
old = '''static bool a52_r269_is_composer_task(void)\n{\n\treturn current->group_leader &&\n\t\t!strncmp(current->group_leader->comm, "composer", 8);\n}\n'''
new = '''extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\n\nstatic bool a52_r269_is_composer_task(void)\n{\n\treturn a52_ackfr_phase269_is_composer_tgid(current->tgid);\n}\n'''
msm = one(msm, old, new, 'replace ambiguous comm matcher')
msmp.write_text(msm, encoding='utf-8')

print('A52_PHASE269_EXACT_COMPOSER_TGID_V1')
