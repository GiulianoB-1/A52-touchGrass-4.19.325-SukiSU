#!/usr/bin/env python3
"""Phase 240: freeze exact GPU-CX registration/probe evidence for late replay.

Phase 239 hardware proved that the CX platform device performs an initial
__device_attach() before any matching qcom,gdsc driver is registered, while the
later state still shows GX bound and CX unbound.  The remaining decisive
registration/probe records occur in the mid-boot ramoops retention hole.

This overlay is diagnostic only.  It adds a dedicated append-only latch in the
existing recorder and two self-identifying driver-side attach records for the
exact 3d9106c.qcom,gdsc / a52-legacy-gdsc-regulator pair.  Match/probe/supplier
return values, initcall levels, driver ordering and device links are untouched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DD = Path("drivers/base/dd.c")
MARKER = "A52_PHASE240_CX_FROZEN_LATCH_V1"
DD_MARKER = "A52_PHASE240_CX_DRIVER_ATTACH_LATCH_V1"

RECORDER_MARKER_ANCHOR = "\t * A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1\n"
RECORDER_MARKER_NEW = RECORDER_MARKER_ANCHOR + f"\t * {MARKER}\n"

FILTER_OLD = '''\tif (strncmp(fmt, "G238", 4) &&\n\t    strncmp(fmt, "KGPPOST", 7) &&\n'''
FILTER_NEW = '''\tif (strncmp(fmt, "CXF240", 6) &&\n\t    strncmp(fmt, "A52GDSC", 7) &&\n\t    strncmp(fmt, "G238", 4) &&\n\t    strncmp(fmt, "KGPPOST", 7) &&\n'''

RECORD_FN = "void a52_ackfr_record(const char *fmt, ...)\n{\n"
LATCH_HELPERS = r'''/* A52_PHASE240_CX_FROZEN_LATCH_V1
 * Dedicated first-event latch: unlike the general KGPPOST journal, this is
 * selected only for the CX provider path and replayed independently.  Entries
 * never overwrite older entries.
 */
#define A52_R240_CXF_CAPACITY 96U
#define A52_R240_REPLAY_TICK_A 155U
#define A52_R240_REPLAY_TICK_B 170U

static char a52_r240_cxf[A52_R240_CXF_CAPACITY][A52_R179_MESSAGE_LEN];
static unsigned int a52_r240_cxf_count;
static atomic_t a52_r240_cxf_seen = ATOMIC_INIT(0);
static atomic_t a52_r240_cxf_replay = ATOMIC_INIT(0);
static DEFINE_SPINLOCK(a52_r240_cxf_lock);

static bool a52_r240_cxf_select(const char *message)
{
	if (!message)
		return false;
	if (!strncmp(message, "CXF240 ", 7))
		return true;
	if (!strncmp(message, "A52GDSC", 7) &&
	    (strstr(message, "CX_") || strstr(message, "gpu_cx") ||
	     strstr(message, "3d9106c")))
		return true;
	if (!strncmp(message, "G238 D", 6) && strstr(message, "3d9106c"))
		return true;
	if (!strncmp(message, "G238 P", 6) && strstr(message, "3d9106c"))
		return true;
	if (!strncmp(message, "G238 GD", 7) && strstr(message, "3d9106c"))
		return true;
	if (!strncmp(message, "KGPPOST 230 ", 12) &&
	    (strstr(message, "cxw ") || strstr(message, "3d9106c") ||
	     strstr(message, "a52-legacy-gdsc")))
		return true;
	return false;
}

static void a52_r240_cxf_latch(const char *message)
{
	unsigned long irq_flags;
	unsigned int index;

	if (atomic_read(&a52_r240_cxf_replay) || !a52_r240_cxf_select(message))
		return;
	atomic_inc(&a52_r240_cxf_seen);
	spin_lock_irqsave(&a52_r240_cxf_lock, irq_flags);
	index = a52_r240_cxf_count;
	if (index < A52_R240_CXF_CAPACITY) {
		strscpy(a52_r240_cxf[index], message,
			sizeof(a52_r240_cxf[index]));
		a52_r240_cxf_count = index + 1;
	}
	spin_unlock_irqrestore(&a52_r240_cxf_lock, irq_flags);
}

static void a52_r240_cxf_replay(unsigned int tick)
{
	char message[A52_R179_MESSAGE_LEN];
	unsigned long irq_flags;
	unsigned int count;
	unsigned int index;
	unsigned int seen;

	if (tick != A52_R240_REPLAY_TICK_A && tick != A52_R240_REPLAY_TICK_B)
		return;

	spin_lock_irqsave(&a52_r240_cxf_lock, irq_flags);
	count = a52_r240_cxf_count;
	spin_unlock_irqrestore(&a52_r240_cxf_lock, irq_flags);
	seen = (unsigned int)atomic_read(&a52_r240_cxf_seen);

	atomic_set(&a52_r240_cxf_replay, 1);
	a52_ackfr_record("CXF240 replay-begin t=%u kept=%u seen=%u",
			tick, count, seen);
	for (index = 0; index < count; index++) {
		spin_lock_irqsave(&a52_r240_cxf_lock, irq_flags);
		strscpy(message, a52_r240_cxf[index], sizeof(message));
		spin_unlock_irqrestore(&a52_r240_cxf_lock, irq_flags);
		a52_ackfr_record("CXF240 replay i=%u %.96s", index, message);
	}
	a52_ackfr_record("CXF240 replay-end t=%u kept=%u seen=%u",
			tick, count, seen);
	atomic_set(&a52_r240_cxf_replay, 0);
}

'''

FORMAT_OLD = '''\tva_start(args, fmt);\n\tvscnprintf(event.message, sizeof(event.message), fmt, args);\n\tva_end(args);\n\ta52_r230_journal_message(event.message);\n'''
FORMAT_NEW = '''\tva_start(args, fmt);\n\tvscnprintf(event.message, sizeof(event.message), fmt, args);\n\tva_end(args);\n\ta52_r240_cxf_latch(event.message);\n\ta52_r230_journal_message(event.message);\n'''

HEARTBEAT_OLD = '''\ttick = (unsigned int)atomic_inc_return(&a52_r179_heartbeat_count);\n\ta52_r230_replay_journal(tick);\n'''
HEARTBEAT_NEW = '''\ttick = (unsigned int)atomic_inc_return(&a52_r179_heartbeat_count);\n\ta52_r240_cxf_replay(tick);\n\ta52_r230_replay_journal(tick);\n'''

SUPPLIER_HELPER_ANCHOR = r'''/* A52_PHASE238_CX_DRIVER_WALK_V1 */
static atomic_t a52_r238_cx_walk_records = ATOMIC_INIT(0);
'''

SUPPLIER_HELPER_NEW = SUPPLIER_HELPER_ANCHOR + r'''
/* A52_PHASE240_CX_SUPPLIER_GATE_V1 */
static void a52_r240_cx_supplier_snapshot(struct device *dev,
		struct device_driver *drv)
{
	struct device_link *link;
	unsigned int n = 0;

	if (!a52_r238_cx_dd_pair(dev, drv))
		return;
	a52_ackfr_record("CXF240 sup-in d=%.20s r=%.20s ls=%d",
			dev_name(dev), drv->name, dev->links.status);
	list_for_each_entry(link, &dev->links.suppliers, c_node) {
		const char *sname;
		const char *sdrv;

		if (++n > 24) {
			a52_ackfr_record("CXF240 sup-limit n=%u", n);
			break;
		}
		sname = link->supplier ? dev_name(link->supplier) : "-";
		sdrv = link->supplier && link->supplier->driver &&
			link->supplier->driver->name ?
			link->supplier->driver->name : "-";
		a52_ackfr_record(
			"CXF240 sup n=%u s=%.20s r=%.20s st=%u fl=%x ds=%d",
			n, sname, sdrv, link->status, link->flags,
			link->supplier ? link->supplier->links.status : -1);
	}
}
'''

SUPPLIER_CALL_OLD = r'''	a52_g238_dd_dump_suppliers(dev, drv);
	ret = device_links_check_suppliers(dev);
	a52_g238_dd_supplier_result(dev, ret);
'''
SUPPLIER_CALL_NEW = r'''	a52_g238_dd_dump_suppliers(dev, drv);
	a52_r240_cx_supplier_snapshot(dev, drv);
	ret = device_links_check_suppliers(dev);
	if (a52_r238_cx_dd_pair(dev, drv))
		a52_ackfr_record("CXF240 sup-out d=%.20s rc=%d ls=%d",
			dev_name(dev), ret, dev->links.status);
	a52_g238_dd_supplier_result(dev, ret);
'''

DRIVER_MATCH_OLD = r'''\tret = driver_match_device(drv, dev);
\tif (a52_r230_gpu_pair(dev, drv))
\t\tA52_R230_DD("ad path=drv match=%d dead=%d cur=%.16s", ret,
\t\t\tdev->p ? dev->p->dead : -1,
\t\t\tdev->driver && dev->driver->name ? dev->driver->name : "-");
'''.replace('\\t', '\t').replace('\\n', '\n')
DRIVER_MATCH_NEW = r'''\tret = driver_match_device(drv, dev);
\tif (a52_r238_cx_dd_pair(dev, drv))
\t\ta52_ackfr_record("CXF240 drv-match d=%.20s r=%.20s rc=%d dead=%d cur=%.16s",
\t\t\tdev_name(dev), drv->name, ret,
\t\t\tdev->p ? dev->p->dead : -1,
\t\t\tdev->driver && dev->driver->name ? dev->driver->name : "-");
\tif (a52_r230_gpu_pair(dev, drv))
\t\tA52_R230_DD("ad path=drv match=%d dead=%d cur=%.16s", ret,
\t\t\tdev->p ? dev->p->dead : -1,
\t\t\tdev->driver && dev->driver->name ? dev->driver->name : "-");
'''.replace('\\t', '\t').replace('\\n', '\n')

DRIVER_PROBE_OLD = r'''\tret = device_driver_attach(drv, dev);
\tif (a52_r230_gpu_pair(dev, drv))
\t\tA52_R230_DD("ad path=drv probe=%d bound=%.16s", ret,
\t\t\tdev->driver && dev->driver->name ? dev->driver->name : "-");

\treturn 0;
}
'''.replace('\\t', '\t').replace('\\n', '\n')
DRIVER_PROBE_NEW = r'''\tret = device_driver_attach(drv, dev);
\tif (a52_r238_cx_dd_pair(dev, drv))
\t\ta52_ackfr_record("CXF240 drv-probe d=%.20s r=%.20s rc=%d bound=%.16s",
\t\t\tdev_name(dev), drv->name, ret,
\t\t\tdev->driver && dev->driver->name ? dev->driver->name : "-");
\tif (a52_r230_gpu_pair(dev, drv))
\t\tA52_R230_DD("ad path=drv probe=%d bound=%.16s", ret,
\t\t\tdev->driver && dev->driver->name ? dev->driver->name : "-");

\treturn 0;
}
'''.replace('\\t', '\t').replace('\\n', '\n')

DRIVER_WALK_OLD = r'''int driver_attach(struct device_driver *drv)
{
\tint ret;

\tif (a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv))
\t\tA52_R230_DD("walk-in r=%.16s bus=%.16s", drv->name,
\t\t\tdrv->bus && drv->bus->name ? drv->bus->name : "-");
\tret = bus_for_each_dev(drv->bus, NULL, drv, __driver_attach);
\tif (a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv))
\t\tA52_R230_DD("walk-out r=%.16s rc=%d", drv->name, ret);
\treturn ret;
}
'''
DRIVER_WALK_NEW = r'''int driver_attach(struct device_driver *drv)
{
\tint ret;

\tif (a52_r238_cx_walk_drv(drv))
\t\ta52_ackfr_record("CXF240 drvwalk-in r=%.24s bus=%.16s",
\t\t\tdrv->name, drv->bus && drv->bus->name ? drv->bus->name : "-");
\tif (a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv))
\t\tA52_R230_DD("walk-in r=%.16s bus=%.16s", drv->name,
\t\t\tdrv->bus && drv->bus->name ? drv->bus->name : "-");
\tret = bus_for_each_dev(drv->bus, NULL, drv, __driver_attach);
\tif (a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv))
\t\tA52_R230_DD("walk-out r=%.16s rc=%d", drv->name, ret);
\tif (a52_r238_cx_walk_drv(drv))
\t\ta52_ackfr_record("CXF240 drvwalk-out r=%.24s rc=%d", drv->name, ret);
\treturn ret;
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if "A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1" not in text:
        raise RuntimeError(f"{label}: Phase 239 identity marker missing")
    text = replace_once(text, RECORDER_MARKER_ANCHOR, RECORDER_MARKER_NEW,
                        f"{label}: marker")
    text = replace_once(text, FILTER_OLD, FILTER_NEW, f"{label}: filter")
    text = replace_once(text, RECORD_FN, LATCH_HELPERS + RECORD_FN,
                        f"{label}: latch helpers")
    text = replace_once(text, FORMAT_OLD, FORMAT_NEW, f"{label}: latch hook")
    text = replace_once(text, HEARTBEAT_OLD, HEARTBEAT_NEW,
                        f"{label}: replay hook")
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER,
        "A52_R240_CXF_CAPACITY 96U",
        'strncmp(fmt, "CXF240", 6)',
        'strncmp(fmt, "A52GDSC", 7)',
        'strstr(message, "3d9106c")',
        'a52_r240_cxf_latch(event.message);',
        'a52_r240_cxf_replay(tick);',
        'a52_ackfr_record("CXF240 replay-begin',
        'a52_ackfr_record("CXF240 replay i=',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_dd(text: str, label: str) -> str:
    if DD_MARKER in text:
        validate_dd(text, label)
        return text
    if "A52_PHASE238_CX_DRIVER_WALK_V1" not in text or \
       "A52_PHASE238_CX_JOURNAL_DD_V1" not in text:
        raise RuntimeError(f"{label}: Phase 238 CX driver-walk/journal markers missing")
    text = replace_once(text, SUPPLIER_HELPER_ANCHOR, SUPPLIER_HELPER_NEW,
                        f"{label}: supplier helper")
    text = replace_once(text, SUPPLIER_CALL_OLD, SUPPLIER_CALL_NEW,
                        f"{label}: supplier gate")
    text = replace_once(text, DRIVER_MATCH_OLD,
                        f"/* {DD_MARKER} */\n" + DRIVER_MATCH_NEW,
                        f"{label}: driver-side match")
    text = replace_once(text, DRIVER_PROBE_OLD, DRIVER_PROBE_NEW,
                        f"{label}: driver-side probe")
    text = replace_once(text, DRIVER_WALK_OLD, DRIVER_WALK_NEW,
                        f"{label}: custom driver walk")
    validate_dd(text, label)
    return text


def validate_dd(text: str, label: str) -> None:
    for token in (
        DD_MARKER,
        "A52_PHASE240_CX_SUPPLIER_GATE_V1",
        'CXF240 sup-in d=%.20s r=%.20s ls=%d',
        'CXF240 sup n=%u s=%.20s r=%.20s st=%u fl=%x ds=%d',
        'CXF240 sup-out d=%.20s rc=%d ls=%d',
        'CXF240 drvwalk-in r=%.24s',
        'CXF240 drvwalk-out r=%.24s rc=%d',
        'CXF240 drv-match d=%.20s r=%.20s rc=%d',
        'CXF240 drv-probe d=%.20s r=%.20s rc=%d',
        'a52_r238_cx_dd_pair(dev, drv)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def root_matches(root: Path) -> bool:
    rec = root / RECORDER
    dd = root / DD
    if not rec.is_file() or not dd.is_file():
        return False
    rtext = rec.read_text(encoding="utf-8")
    dtext = dd.read_text(encoding="utf-8")
    return ("A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1" in rtext and
            "A52_PHASE238_CX_DRIVER_WALK_V1" in dtext and
            "A52_PHASE238_CX_JOURNAL_DD_V1" in dtext)


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches = [root for root in candidate_roots(args, base) if root_matches(root)]
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(root) for root in unique) or "none"
        raise RuntimeError(
            "expected exactly one generated Phase 239 source root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def self_test() -> None:
    rec_fixture = (
        "/* recorder */\n"
        + RECORDER_MARKER_ANCHOR
        + FILTER_OLD
        + '''\t    strncmp(fmt, "OFPOP", 5))\n\t\treturn;\n\n'''
        + RECORD_FN
        + '''\tstruct a52_r179_event event;\n\tva_list args;\n'''
        + FORMAT_OLD
        + '''}\n\nstatic atomic_t a52_r179_heartbeat_count = ATOMIC_INIT(0);\n'''
          '''static void a52_r179_heartbeat_fn(struct work_struct *work)\n{\n'''
          '''\tunsigned int tick;\n\n'''
        + HEARTBEAT_OLD
        + '''}\n'''
    )
    rec = patch_recorder(rec_fixture, "fixture/recorder.c")
    if patch_recorder(rec, "fixture/recorder.c/idempotent") != rec:
        raise AssertionError("Phase 240 recorder patch is not idempotent")

    dd_fixture = (
        "/* A52_PHASE238_CX_JOURNAL_DD_V1 */\n"
        + SUPPLIER_HELPER_ANCHOR
        + "\n"
        + SUPPLIER_CALL_OLD
        + "\n"
        + DRIVER_MATCH_OLD
        + DRIVER_PROBE_OLD
        + DRIVER_WALK_OLD
    )
    dd = patch_dd(dd_fixture, "fixture/dd.c")
    if patch_dd(dd, "fixture/dd.c/idempotent") != dd:
        raise AssertionError("Phase 240 DD patch is not idempotent")

    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        root = repo / "gki/common"
        rec_path = root / RECORDER
        dd_path = root / DD
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        dd_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(rec_fixture, encoding="utf-8")
        dd_path.write_text(dd_fixture, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != root.resolve():
            raise AssertionError(f"locator chose {found}, expected {root}")

    print(
        "Phase 240 CX frozen registration/probe latch self-test: PASS",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    rec_path = root / RECORDER
    dd_path = root / DD
    rec_path.write_text(
        patch_recorder(rec_path.read_text(encoding="utf-8"), str(rec_path)),
        encoding="utf-8",
    )
    dd_path.write_text(
        patch_dd(dd_path.read_text(encoding="utf-8"), str(dd_path)),
        encoding="utf-8",
    )
    print(
        "Phase 240 CX frozen latch applied: append-only selected provider/driver "
        "evidence will replay at heartbeat ticks 155 and 170",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
