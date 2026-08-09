#!/usr/bin/env python3
"""Phase 241: broad frozen GPU-CX failure-corridor recorder.

Diagnostic-only overlay over Phase 240. It freezes the earliest evidence in
separate retention buckets so late defer traffic cannot evict earlier
platform-population, driver-registration, match/probe, or supplier/GDSC data.
It also closes upstream visibility gaps with target-only OF device creation and
driver_register() tracing, and binds replay to the actual function containing
the surviving ``HB tick=%u`` record.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OF_PLATFORM = Path("drivers/of/platform.c")
DRIVER = Path("drivers/base/driver.c")

MARKER = "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1"
OF_MARKER = "A52_PHASE241_OF_GPU_CREATE_TRACE_V1"
DRIVER_MARKER = "A52_PHASE241_GPU_DRIVER_REGISTER_TRACE_V1"
IDENTITY_ANCHOR = "\t * A52_PHASE240_CX_SUPPLIER_GATE_LATCH_IDENTITY_V1\n"

FILTER_OLD = '''\tif (strncmp(fmt, "CXF240", 6) &&\n\t    strncmp(fmt, "A52GDSC", 7) &&\n'''
FILTER_NEW = '''\tif (strncmp(fmt, "CXF241", 6) &&\n\t    strncmp(fmt, "CXF240", 6) &&\n\t    strncmp(fmt, "A52GDSC", 7) &&\n'''
RECORD_FN = "void a52_ackfr_record(const char *fmt, ...)\n{\n"
LATCH_HOOK_OLD = '''\ta52_r240_cxf_latch(event.message);\n\ta52_r230_journal_message(event.message);\n'''
LATCH_HOOK_NEW = '''\ta52_r241_corridor_latch(event.message);\n\ta52_r240_cxf_latch(event.message);\n\ta52_r230_journal_message(event.message);\n'''

HELPERS = r'''/* A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1
 * First-event retention in independent classes. Later defer/supplier spam
 * cannot overwrite early population/registration evidence.
 */
#define A52_R241_POP_CAPACITY 24U
#define A52_R241_DRV_CAPACITY 32U
#define A52_R241_PRB_CAPACITY 48U
#define A52_R241_SUP_CAPACITY 48U
#define A52_R241_REPLAY_TICK_A 155U
#define A52_R241_REPLAY_TICK_B 170U

#define A52_R241_POP 0x1U
#define A52_R241_DRV 0x2U
#define A52_R241_PRB 0x4U
#define A52_R241_SUP 0x8U

static char a52_r241_pop[A52_R241_POP_CAPACITY][A52_R179_MESSAGE_LEN];
static char a52_r241_drv[A52_R241_DRV_CAPACITY][A52_R179_MESSAGE_LEN];
static char a52_r241_prb[A52_R241_PRB_CAPACITY][A52_R179_MESSAGE_LEN];
static char a52_r241_sup[A52_R241_SUP_CAPACITY][A52_R179_MESSAGE_LEN];
static unsigned int a52_r241_pop_count;
static unsigned int a52_r241_drv_count;
static unsigned int a52_r241_prb_count;
static unsigned int a52_r241_sup_count;
static atomic_t a52_r241_pop_seen = ATOMIC_INIT(0);
static atomic_t a52_r241_drv_seen = ATOMIC_INIT(0);
static atomic_t a52_r241_prb_seen = ATOMIC_INIT(0);
static atomic_t a52_r241_sup_seen = ATOMIC_INIT(0);
static atomic_t a52_r241_replaying = ATOMIC_INIT(0);
static DEFINE_SPINLOCK(a52_r241_pop_lock);
static DEFINE_SPINLOCK(a52_r241_drv_lock);
static DEFINE_SPINLOCK(a52_r241_prb_lock);
static DEFINE_SPINLOCK(a52_r241_sup_lock);

static bool a52_r241_gpu_context(const char *message)
{
	if (!message)
		return false;
	return strstr(message, "3d9106c") || strstr(message, "3d9100c") ||
	       strstr(message, "3d90000") || strstr(message, "3d00000") ||
	       strstr(message, "gpu_cx") || strstr(message, "gpu_gx") ||
	       strstr(message, "gpucc") || strstr(message, "kgsl") ||
	       strstr(message, "a52-legacy-gdsc") || strstr(message, "A52GDSC");
}

static unsigned int a52_r241_classify(const char *message)
{
	unsigned int mask = 0;
	bool gpu;

	if (!message || !strncmp(message, "CXF241 ", 7))
		return 0;
	gpu = a52_r241_gpu_context(message);

	if (!strncmp(message, "OFPOP ", 6) ||
	    (!strncmp(message, "G238 P", 6) && gpu) ||
	    (!strncmp(message, "P3P enter", 9) && gpu) ||
	    (!strncmp(message, "CXF241 create-", 15) && gpu))
		mask |= A52_R241_POP;

	if ((!strncmp(message, "CXF241 dreg-", 13) && gpu) ||
	    (!strncmp(message, "CXF240 drv", 10) && gpu) ||
	    (!strncmp(message, "KGPPOST 230 ", 12) &&
	     (strstr(message, "cxw ") || strstr(message, "walk-") || gpu)))
		mask |= A52_R241_DRV;

	if ((gpu && (!strncmp(message, "G238 D", 6) ||
	             !strncmp(message, "G238 P", 6) ||
	             !strncmp(message, "G238 GD", 7) ||
	             !strncmp(message, "A52GDSC", 7) ||
	             !strncmp(message, "CXF240 drv", 10))) ||
	    (!strncmp(message, "KGPPOST 230 ", 12) &&
	     (strstr(message, "cxw ") || strstr(message, "dp-") ||
	      strstr(message, "rp-") || strstr(message, "ad path=") || gpu)))
		mask |= A52_R241_PRB;

	if ((gpu && (!strncmp(message, "G238 D", 6) ||
	             !strncmp(message, "G238 GD", 7) ||
	             !strncmp(message, "A52GDSC", 7) ||
	             !strncmp(message, "CXF240 sup", 10))) ||
	    (gpu && (strstr(message, "vdd_parent") || strstr(message, "sl-") ||
	             strstr(message, "dl s=") || strstr(message, "supplier"))))
		mask |= A52_R241_SUP;
	return mask;
}

static void a52_r241_store(char (*records)[A52_R179_MESSAGE_LEN],
			   unsigned int capacity, unsigned int *count,
			   atomic_t *seen, spinlock_t *lock,
			   const char *message)
{
	unsigned long irq_flags;
	unsigned int index;

	atomic_inc(seen);
	spin_lock_irqsave(lock, irq_flags);
	index = *count;
	if (index < capacity) {
		strscpy(records[index], message, A52_R179_MESSAGE_LEN);
		*count = index + 1;
	}
	spin_unlock_irqrestore(lock, irq_flags);
}

static void a52_r241_corridor_latch(const char *message)
{
	unsigned int mask;

	if (atomic_read(&a52_r241_replaying))
		return;
	mask = a52_r241_classify(message);
	if (mask & A52_R241_POP)
		a52_r241_store(a52_r241_pop, A52_R241_POP_CAPACITY,
				&a52_r241_pop_count, &a52_r241_pop_seen,
				&a52_r241_pop_lock, message);
	if (mask & A52_R241_DRV)
		a52_r241_store(a52_r241_drv, A52_R241_DRV_CAPACITY,
				&a52_r241_drv_count, &a52_r241_drv_seen,
				&a52_r241_drv_lock, message);
	if (mask & A52_R241_PRB)
		a52_r241_store(a52_r241_prb, A52_R241_PRB_CAPACITY,
				&a52_r241_prb_count, &a52_r241_prb_seen,
				&a52_r241_prb_lock, message);
	if (mask & A52_R241_SUP)
		a52_r241_store(a52_r241_sup, A52_R241_SUP_CAPACITY,
				&a52_r241_sup_count, &a52_r241_sup_seen,
				&a52_r241_sup_lock, message);
}

static void a52_r241_replay_bucket(const char *tag,
			   char (*records)[A52_R179_MESSAGE_LEN],
			   unsigned int count, spinlock_t *lock)
{
	char message[A52_R179_MESSAGE_LEN];
	unsigned long irq_flags;
	unsigned int index;

	for (index = 0; index < count; index++) {
		spin_lock_irqsave(lock, irq_flags);
		strscpy(message, records[index], sizeof(message));
		spin_unlock_irqrestore(lock, irq_flags);
		a52_ackfr_record("CXF241 %s i=%u %.88s", tag, index, message);
	}
}

static void a52_r241_corridor_replay(unsigned int tick)
{
	unsigned int pc, dc, qc, sc;
	unsigned int ps, ds, qs, ss;

	if (tick != A52_R241_REPLAY_TICK_A && tick != A52_R241_REPLAY_TICK_B)
		return;
	pc = READ_ONCE(a52_r241_pop_count);
	dc = READ_ONCE(a52_r241_drv_count);
	qc = READ_ONCE(a52_r241_prb_count);
	sc = READ_ONCE(a52_r241_sup_count);
	ps = (unsigned int)atomic_read(&a52_r241_pop_seen);
	ds = (unsigned int)atomic_read(&a52_r241_drv_seen);
	qs = (unsigned int)atomic_read(&a52_r241_prb_seen);
	ss = (unsigned int)atomic_read(&a52_r241_sup_seen);

	atomic_set(&a52_r241_replaying, 1);
	a52_ackfr_record("CXF241 replay-begin t=%u pop=%u/%u drv=%u/%u prb=%u/%u sup=%u/%u",
			 tick, pc, ps, dc, ds, qc, qs, sc, ss);
	if (tick == A52_R241_REPLAY_TICK_A) {
		a52_r241_replay_bucket("pop", a52_r241_pop, pc, &a52_r241_pop_lock);
		a52_r241_replay_bucket("drv", a52_r241_drv, dc, &a52_r241_drv_lock);
		a52_r241_replay_bucket("prb", a52_r241_prb, qc, &a52_r241_prb_lock);
		a52_r241_replay_bucket("sup", a52_r241_sup, sc, &a52_r241_sup_lock);
	}
	a52_ackfr_record("CXF241 stats t=%u pop=%u/%u drv=%u/%u prb=%u/%u sup=%u/%u",
			 tick, pc, ps, dc, ds, qc, qs, sc, ss);
	a52_ackfr_record("CXF241 replay-end t=%u", tick);
	atomic_set(&a52_r241_replaying, 0);
}

'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def mask_c(text: str) -> str:
    out = list(text)
    state = "code"
    i = 0
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                out[i] = out[i + 1] = " "; state = "block"; i += 2; continue
            if c == "/" and n == "/":
                out[i] = out[i + 1] = " "; state = "line"; i += 2; continue
            if c == '"': out[i] = " "; state = "string"
            elif c == "'": out[i] = " "; state = "char"
        elif state == "block":
            if c == "*" and n == "/":
                out[i] = out[i + 1] = " "; state = "code"; i += 2; continue
            if c != "\n": out[i] = " "
        elif state == "line":
            if c == "\n": state = "code"
            else: out[i] = " "
        else:
            quote = '"' if state == "string" else "'"
            if c == "\\":
                out[i] = " "
                if i + 1 < len(text): out[i + 1] = " "
                i += 2; continue
            if c == quote: out[i] = " "; state = "code"
            elif c != "\n": out[i] = " "
        i += 1
    return "".join(out)


def enclosing_top_level_block(text: str, position: int, label: str) -> tuple[int, int]:
    masked = mask_c(text)
    depth = 0
    opening = -1
    for i, c in enumerate(masked[:position]):
        if c == "{":
            if depth == 0: opening = i
            depth += 1
        elif c == "}": depth -= 1
    if depth <= 0 or opening < 0:
        raise RuntimeError(f"{label}: marker is not inside a top-level function body")
    d = 0
    for i in range(opening, len(masked)):
        if masked[i] == "{": d += 1
        elif masked[i] == "}":
            d -= 1
            if d == 0: return opening, i
    raise RuntimeError(f"{label}: unterminated enclosing function")


def validate_live_heartbeat(text: str, label: str) -> None:
    needle = '"HB tick=%u'
    if text.count(needle) != 1:
        raise RuntimeError(f"{label}: expected exactly one live HB format, found {text.count(needle)}")
    marker = text.index(needle)
    opening, closing = enclosing_top_level_block(text, marker, label)
    body = text[opening + 1:closing]
    tokens = (
        'a52_ackfr_record("HB tick=%u',
        'a52_ackfr_record("CXF241 live t=%u", tick);',
        'a52_r241_corridor_replay(tick);',
    )
    for token in tokens:
        if body.count(token) != 1:
            raise RuntimeError(f"{label}: live heartbeat body expected one {token!r}, found {body.count(token)}")
    if not (body.find(tokens[0]) < body.find(tokens[1]) < body.find(tokens[2])):
        raise RuntimeError(f"{label}: live/replay must follow surviving HB record")
    if "a52_r240_cxf_replay(tick);" in body:
        raise RuntimeError(f"{label}: obsolete Phase240 replay remains in live heartbeat")


def patch_live_heartbeat(text: str, label: str) -> str:
    live = 'a52_ackfr_record("CXF241 live t=%u", tick);'
    if live in text:
        validate_live_heartbeat(text, label)
        return text
    needle = '"HB tick=%u'
    if text.count(needle) != 1:
        raise RuntimeError(f"{label}: expected exactly one live HB format, found {text.count(needle)}")
    marker = text.index(needle)
    opening, closing = enclosing_top_level_block(text, marker, label)
    body = text[opening + 1:closing]
    if "a52_r240_cxf_replay(tick);" in body:
        body = body.replace("\ta52_r240_cxf_replay(tick);\n", "", 1)
        text = text[:opening + 1] + body + text[closing:]
        marker = text.index(needle)
        opening, closing = enclosing_top_level_block(text, marker, label)
    semi = text.find(";", marker, closing)
    if semi < 0:
        raise RuntimeError(f"{label}: HB recorder call terminator missing")
    insertion = (
        "\n\tif (tick == A52_R241_REPLAY_TICK_A ||\n"
        "\t    tick == A52_R241_REPLAY_TICK_B) {\n"
        "\t\ta52_ackfr_record(\"CXF241 live t=%u\", tick);\n"
        "\t\ta52_r241_corridor_replay(tick);\n"
        "\t}"
    )
    text = text[:semi + 1] + insertion + text[semi + 1:]
    validate_live_heartbeat(text, label)
    return text


def patch_recorder(text: str, label: str) -> str:
    if MARKER not in text:
        if "A52_PHASE240_CX_SUPPLIER_GATE_LATCH_IDENTITY_V1" not in text:
            raise RuntimeError(f"{label}: Phase240 identity-chain marker missing")
        text = replace_once(text, IDENTITY_ANCHOR, IDENTITY_ANCHOR + "\t * " + MARKER + "\n", f"{label}: marker")
        text = replace_once(text, FILTER_OLD, FILTER_NEW, f"{label}: filter")
        text = replace_once(text, RECORD_FN, HELPERS + RECORD_FN, f"{label}: helpers")
        text = replace_once(text, LATCH_HOOK_OLD, LATCH_HOOK_NEW, f"{label}: latch hook")
    text = patch_live_heartbeat(text, label)
    for token in (MARKER, "A52_R241_POP_CAPACITY 24U", "A52_R241_DRV_CAPACITY 32U",
                  "A52_R241_PRB_CAPACITY 48U", "A52_R241_SUP_CAPACITY 48U",
                  'strncmp(fmt, "CXF241", 6)', "a52_r241_corridor_latch(event.message);",
                  'a52_ackfr_record("CXF241 replay-begin'):
        if token not in text: raise RuntimeError(f"{label}: missing {token}")
    validate_live_heartbeat(text, label)
    return text


def find_function(text: str, pattern: str, label: str) -> tuple[int, int, int]:
    m = re.search(pattern, text, re.M)
    if not m: raise RuntimeError(f"{label}: function signature not found")
    brace = text.find("{", m.start(), m.end() + 8)
    if brace < 0: raise RuntimeError(f"{label}: opening brace missing")
    masked = mask_c(text)
    depth = 0
    for i in range(brace, len(masked)):
        if masked[i] == "{": depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0: return m.start(), brace, i + 1
    raise RuntimeError(f"{label}: unterminated function")


OF_HELPERS = r'''/* A52_PHASE241_OF_GPU_CREATE_TRACE_V1 */
static bool a52_r241_of_gpu_target(struct device_node *np)
{
	const char *name = np ? np->full_name : NULL;
	return name && (strstr(name, "3d9106c") || strstr(name, "3d9100c") ||
		       strstr(name, "3d90000") || strstr(name, "3d00000"));
}

static struct platform_device *a52_r241_of_create_return(struct device_node *np,
		struct platform_device *pdev, int line)
{
	if (a52_r241_of_gpu_target(np))
		a52_ackfr_record("CXF241 create-out node=%.52s ok=%u dev=%.24s l=%d",
			np->full_name, pdev != NULL,
			pdev ? dev_name(&pdev->dev) : "-", line);
	return pdev;
}

'''


def patch_of_platform(text: str, label: str) -> str:
    if OF_MARKER in text: return text
    if "A52_PHASE237_OFPOP_TRACE_V1" not in text:
        raise RuntimeError(f"{label}: inherited Phase237 OFPOP trace missing")
    pattern = (r"(?:static\s+)?struct\s+platform_device\s*\*\s*"
               r"of_platform_device_create_pdata\s*\([^)]*\)\s*\{")
    start, _, _ = find_function(text, pattern, f"{label}: create_pdata")
    text = text[:start] + OF_HELPERS + text[start:]
    start, _, end = find_function(text, pattern, f"{label}: create_pdata after helpers")
    fn = text[start:end]
    brace = fn.find("{")
    fn = fn[:brace + 1] + ("\n\tif (a52_r241_of_gpu_target(np))\n"
                           "\t\ta52_ackfr_record(\"CXF241 create-in node=%.64s\", np->full_name);\n") + fn[brace + 1:]
    fn = re.sub(r"(?m)^([ \t]*)return\s+([^;\n]+);",
                lambda m: f"{m.group(1)}return a52_r241_of_create_return(np, ({m.group(2).strip()}), __LINE__);", fn)
    text = text[:start] + fn + text[end:]
    for token in (OF_MARKER, 'CXF241 create-in', 'CXF241 create-out',
                  'strstr(name, "3d9106c")', 'strstr(name, "3d9100c")',
                  'strstr(name, "3d90000")', 'strstr(name, "3d00000")'):
        if token not in text: raise RuntimeError(f"{label}: missing {token}")
    return text


DRIVER_HELPERS = r'''/* A52_PHASE241_GPU_DRIVER_REGISTER_TRACE_V1 */
static bool a52_r241_driver_focus(struct device_driver *drv)
{
	const char *name = drv ? drv->name : NULL;
	return name && (strstr(name, "a52-legacy-gdsc") ||
		       strstr(name, "kgsl") || strstr(name, "gpu"));
}

static int a52_r241_driver_register_return(struct device_driver *drv, int rc, int line)
{
	if (a52_r241_driver_focus(drv))
		a52_ackfr_record("CXF241 dreg-out r=%.32s rc=%d l=%d", drv->name, rc, line);
	return rc;
}

'''


def patch_driver(text: str, label: str) -> str:
    if DRIVER_MARKER in text: return text
    include = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if include not in text:
        matches = list(re.finditer(r"^#include[^\n]*\n", text, re.M))
        if not matches: raise RuntimeError(f"{label}: no include anchor")
        pos = matches[-1].end(); text = text[:pos] + include + text[pos:]
    pattern = r"int\s+driver_register\s*\(\s*struct\s+device_driver\s*\*\s*drv\s*\)\s*\{"
    start, _, _ = find_function(text, pattern, f"{label}: driver_register")
    text = text[:start] + DRIVER_HELPERS + text[start:]
    start, _, end = find_function(text, pattern, f"{label}: driver_register after helpers")
    fn = text[start:end]
    brace = fn.find("{")
    fn = fn[:brace + 1] + ("\n\tif (a52_r241_driver_focus(drv))\n"
                           "\t\ta52_ackfr_record(\"CXF241 dreg-in r=%.32s bus=%.16s\",\n"
                           "\t\t\tdrv->name, drv->bus && drv->bus->name ? drv->bus->name : \"-\");\n") + fn[brace + 1:]
    fn = re.sub(r"(?m)^([ \t]*)return\s+([^;\n]+);",
                lambda m: f"{m.group(1)}return a52_r241_driver_register_return(drv, ({m.group(2).strip()}), __LINE__);", fn)
    text = text[:start] + fn + text[end:]
    for token in (DRIVER_MARKER, 'CXF241 dreg-in', 'CXF241 dreg-out',
                  'strstr(name, "a52-legacy-gdsc")'):
        if token not in text: raise RuntimeError(f"{label}: missing {token}")
    return text


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"): continue
        p = Path(value)
        if not p.is_absolute(): p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen: seen.add(key); out.append(root)
    return out


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        paths = (root / RECORDER, root / OF_PLATFORM, root / DRIVER)
        if not all(p.is_file() for p in paths): continue
        rt = paths[0].read_text(encoding="utf-8")
        ot = paths[1].read_text(encoding="utf-8")
        if "A52_PHASE240_CX_SUPPLIER_GATE_LATCH_IDENTITY_V1" not in rt or "A52_PHASE237_OFPOP_TRACE_V1" not in ot: continue
        key = root.resolve()
        if key not in seen: seen.add(key); unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(r) for r in unique) or "none"
        raise RuntimeError(f"expected exactly one generated Phase240 source root, found {len(unique)}: {rendered}")
    return unique[0]


def self_test() -> None:
    for token in ("A52_R241_POP_CAPACITY 24U", "A52_R241_DRV_CAPACITY 32U",
                  "A52_R241_PRB_CAPACITY 48U", "A52_R241_SUP_CAPACITY 48U",
                  "CXF241 replay-begin", "CXF241 stats"):
        if token not in HELPERS: raise AssertionError(token)
    if "(?:static\\s+)?struct" not in Path(__file__).read_text(encoding="utf-8"):
        raise AssertionError("OF create matcher lost static/exported compatibility")
    print("Phase 241 broad CX corridor latch self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]: self_test(); return 0
    root = locate_generated(sys.argv[1:])
    rec, ofp, drv = root / RECORDER, root / OF_PLATFORM, root / DRIVER
    rec.write_text(patch_recorder(rec.read_text(encoding="utf-8"), str(rec)), encoding="utf-8")
    ofp.write_text(patch_of_platform(ofp.read_text(encoding="utf-8"), str(ofp)), encoding="utf-8")
    drv.write_text(patch_driver(drv.read_text(encoding="utf-8"), str(drv)), encoding="utf-8")
    print("Phase 241 broad CX corridor latch applied: population/registration/probe/supplier evidence frozen; live-HB replay armed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
