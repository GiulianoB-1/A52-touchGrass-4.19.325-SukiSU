#!/usr/bin/env python3
"""Phase 242: compact sticky CX state, no bulk replay.

Hardware Phase 241 proved target platform-device creation but its late bucket
replay did not materialize. Phase 242 keeps all functional Phase 239 behavior
and the Phase 240/241 source-side diagnostics, then reduces the retention
problem to a compact sticky state summary. Existing early diagnostic messages
update fixed atomic state before recorder-capacity rejection. Selected late
heartbeat ticks emit only two compact critical summaries plus, when present,
one frozen unresolved-supplier line. No match/probe/supplier return value,
driver order, initcall level, device link, or recorder transport is changed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MARKER = "A52_PHASE242_CX_STICKY_STATE_V1"
DISABLE_MARKER = "A52_PHASE242_PHASE241_REPLAY_DISABLED_V1"

MARKER_OLD = "\t * A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_IDENTITY_V1\n"
MARKER_NEW = MARKER_OLD + f"\t * {MARKER}\n"
FILTER_OLD = 'if (strncmp(fmt, "CXF241", 6) &&\n'
FILTER_NEW = 'if (strncmp(fmt, "CXF242", 6) &&\n\t    strncmp(fmt, "CXF241", 6) &&\n'
CRIT_OLD = 'return !strncmp(message, "CXF241 ", 7) ||\n'
CRIT_NEW = 'return !strncmp(message, "CXF242 ", 7) ||\n\t       !strncmp(message, "CXF241 ", 7) ||\n'
RECORD_FN = "void a52_ackfr_record(const char *fmt, ...)\n{\n"
LATCH_OLD = "\ta52_r241_corridor_latch(event.message);\n"
LATCH_NEW = "\ta52_r242_sticky_latch(event.message);\n\ta52_r241_corridor_latch(event.message);\n"
HEARTBEAT_OLD = '''\ta52_r228_tripost_snapshot(tick);\n\ta52_ackfr_record("HB tick=%u online=%u run=%lu j=%lu", tick,\n'''
HEARTBEAT_NEW = '''\ta52_r228_tripost_snapshot(tick);\n\ta52_r242_snapshot(tick);\n\ta52_ackfr_record("HB tick=%u online=%u run=%lu j=%lu", tick,\n'''
REPLAY_BLOCK = '''\tif (tick == A52_R241_REPLAY_TICK_A ||\n\t    tick == A52_R241_REPLAY_TICK_B) {\n\t\ta52_ackfr_record("CXF241 live t=%u", tick);\n\t\ta52_r241_corridor_replay(tick);\n\t}\n'''
REPLAY_DISABLED = f'''\t/* {DISABLE_MARKER}\n\t * Phase 241 hardware showed the bulk replay path itself is unreliable.\n\t * Keep source-side latching, but do not execute the bucket replay.\n\t */\n'''

HELPERS = r'''/* A52_PHASE242_CX_STICKY_STATE_V1
 * Fixed state survives the early retention hole without replaying a bucket.
 * -1 is an unseen creation state; 0/1 are observed creation outcomes;
 * -4096 is the explicit unseen return-code sentinel.
 */
#define A52_R242_UNSEEN (-4096)
static atomic_t a52_r242_cx_create = ATOMIC_INIT(-1);
static atomic_t a52_r242_gx_create = ATOMIC_INIT(-1);
static atomic_t a52_r242_dreg_seen = ATOMIC_INIT(0);
static atomic_t a52_r242_dreg_rc = ATOMIC_INIT(A52_R242_UNSEEN);
static atomic_t a52_r242_walk_seen = ATOMIC_INIT(0);
static atomic_t a52_r242_walk_rc = ATOMIC_INIT(A52_R242_UNSEEN);
static atomic_t a52_r242_match_seen = ATOMIC_INIT(0);
static atomic_t a52_r242_match_rc = ATOMIC_INIT(A52_R242_UNSEEN);
static atomic_t a52_r242_probe_seen = ATOMIC_INIT(0);
static atomic_t a52_r242_probe_rc = ATOMIC_INIT(A52_R242_UNSEEN);
static atomic_t a52_r242_sup_seen = ATOMIC_INIT(0);
static atomic_t a52_r242_sup_rc = ATOMIC_INIT(A52_R242_UNSEEN);
static atomic_t a52_r242_gdsc_seen = ATOMIC_INIT(0);
static atomic_t a52_r242_gdsc_rc = ATOMIC_INIT(A52_R242_UNSEEN);
static atomic_t a52_r242_unbound_sup_seen = ATOMIC_INIT(0);
static char a52_r242_unbound_sup[A52_R179_MESSAGE_LEN];
static DEFINE_SPINLOCK(a52_r242_unbound_sup_lock);

static void a52_r242_freeze_unbound_supplier(const char *message)
{
	unsigned long irq_flags;

	if (atomic_read(&a52_r242_unbound_sup_seen))
		return;
	spin_lock_irqsave(&a52_r242_unbound_sup_lock, irq_flags);
	if (!atomic_read(&a52_r242_unbound_sup_seen)) {
		strscpy(a52_r242_unbound_sup, message,
			sizeof(a52_r242_unbound_sup));
		atomic_set(&a52_r242_unbound_sup_seen, 1);
	}
	spin_unlock_irqrestore(&a52_r242_unbound_sup_lock, irq_flags);
}

static void a52_r242_sticky_latch(const char *message)
{
	int value;

	if (!message || !strncmp(message, "CXF242 ", 7))
		return;

	if (!strncmp(message, "CXF241 create-out ", 18)) {
		value = a52_r228_dec(message, "ok=", -1);
		if (strstr(message, "3d9106c"))
			atomic_set(&a52_r242_cx_create, value);
		if (strstr(message, "3d9100c"))
			atomic_set(&a52_r242_gx_create, value);
	}
	if (!strncmp(message, "CXF241 dreg-out ", 16) &&
	    strstr(message, "a52-legacy-gdsc-regulator")) {
		atomic_inc(&a52_r242_dreg_seen);
		atomic_set(&a52_r242_dreg_rc,
			a52_r228_dec(message, "rc=", A52_R242_UNSEEN));
	}
	if (!strncmp(message, "CXF240 drvwalk-in ", 18))
		atomic_inc(&a52_r242_walk_seen);
	if (!strncmp(message, "CXF240 drvwalk-out ", 19))
		atomic_set(&a52_r242_walk_rc,
			a52_r228_dec(message, "rc=", A52_R242_UNSEEN));
	if (!strncmp(message, "CXF240 drv-match ", 17)) {
		atomic_inc(&a52_r242_match_seen);
		atomic_set(&a52_r242_match_rc,
			a52_r228_dec(message, "rc=", A52_R242_UNSEEN));
	}
	if (!strncmp(message, "CXF240 drv-probe ", 17)) {
		atomic_inc(&a52_r242_probe_seen);
		atomic_set(&a52_r242_probe_rc,
			a52_r228_dec(message, "rc=", A52_R242_UNSEEN));
	}
	if (!strncmp(message, "CXF240 sup-out ", 15)) {
		atomic_inc(&a52_r242_sup_seen);
		atomic_set(&a52_r242_sup_rc,
			a52_r228_dec(message, "rc=", A52_R242_UNSEEN));
	}
	if (!strncmp(message, "CXF240 sup n=", 13) && strstr(message, " r=-"))
		a52_r242_freeze_unbound_supplier(message);
	if (!strncmp(message, "A52GDSC CX_VDD_PARENT_GET_V1 ", 29)) {
		atomic_inc(&a52_r242_gdsc_seen);
		atomic_set(&a52_r242_gdsc_rc,
			a52_r228_dec(message, "rc=", A52_R242_UNSEEN));
	}
}

static bool a52_r242_snapshot_tick(unsigned int tick)
{
	return tick == 120 || tick == 140 || tick == 145 || tick == 150 ||
	       tick == 155 || tick == 160 || tick == 165 || tick == 170 ||
	       tick == 175;
}

static void a52_r242_snapshot(unsigned int tick)
{
	char unresolved[A52_R179_MESSAGE_LEN];
	unsigned long irq_flags;

	if (!a52_r242_snapshot_tick(tick))
		return;
	a52_ackfr_record("CXF242 A t=%u c=%d g=%d dr=%d/%d dw=%d/%d dm=%d/%d",
		tick, atomic_read(&a52_r242_cx_create),
		atomic_read(&a52_r242_gx_create),
		atomic_read(&a52_r242_dreg_seen), atomic_read(&a52_r242_dreg_rc),
		atomic_read(&a52_r242_walk_seen), atomic_read(&a52_r242_walk_rc),
		atomic_read(&a52_r242_match_seen), atomic_read(&a52_r242_match_rc));
	a52_ackfr_record("CXF242 B t=%u sp=%d/%d pr=%d/%d gd=%d/%d",
		tick, atomic_read(&a52_r242_sup_seen), atomic_read(&a52_r242_sup_rc),
		atomic_read(&a52_r242_probe_seen), atomic_read(&a52_r242_probe_rc),
		atomic_read(&a52_r242_gdsc_seen), atomic_read(&a52_r242_gdsc_rc));
	if (!atomic_read(&a52_r242_unbound_sup_seen))
		return;
	spin_lock_irqsave(&a52_r242_unbound_sup_lock, irq_flags);
	strscpy(unresolved, a52_r242_unbound_sup, sizeof(unresolved));
	spin_unlock_irqrestore(&a52_r242_unbound_sup_lock, irq_flags);
	a52_ackfr_record("CXF242 U t=%u %.68s", tick, unresolved);
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
        validate(text, label)
        return text
    if "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1" not in text:
        raise RuntimeError(f"{label}: Phase 241 broad-corridor marker missing")
    text = replace_once(text, MARKER_OLD, MARKER_NEW, f"{label}: marker")
    text = replace_once(text, FILTER_OLD, FILTER_NEW, f"{label}: format filter")
    text = replace_once(text, CRIT_OLD, CRIT_NEW, f"{label}: critical filter")
    text = replace_once(text, RECORD_FN, HELPERS + RECORD_FN, f"{label}: helpers")
    text = replace_once(text, LATCH_OLD, LATCH_NEW, f"{label}: sticky latch hook")
    text = replace_once(text, HEARTBEAT_OLD, HEARTBEAT_NEW, f"{label}: pre-HB snapshot")
    text = replace_once(text, REPLAY_BLOCK, REPLAY_DISABLED, f"{label}: disable Phase241 bulk replay")
    text = text.replace(
        "static void a52_r241_corridor_replay(unsigned int tick)",
        "static void __maybe_unused a52_r241_corridor_replay(unsigned int tick)",
        1,
    )
    validate(text, label)
    return text


def validate(text: str, label: str) -> None:
    required = (
        MARKER,
        DISABLE_MARKER,
        'strncmp(fmt, "CXF242", 6)',
        'return !strncmp(message, "CXF242 ", 7) ||',
        'a52_r242_sticky_latch(event.message);',
        'a52_r242_snapshot(tick);',
        'CXF242 A t=%u c=%d g=%d dr=%d/%d dw=%d/%d dm=%d/%d',
        'CXF242 B t=%u sp=%d/%d pr=%d/%d gd=%d/%d',
        'CXF242 U t=%u %.68s',
        'CXF241 create-out ',
        'CXF241 dreg-out ',
        'CXF240 drvwalk-in ',
        'CXF240 drv-match ',
        'CXF240 drv-probe ',
        'CXF240 sup-out ',
        'A52GDSC CX_VDD_PARENT_GET_V1 ',
        '__maybe_unused a52_r241_corridor_replay',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    hb = text.find('a52_ackfr_record("HB tick=%u')
    snap = text.rfind('a52_r242_snapshot(tick);', 0, hb + 1)
    if hb < 0 or snap < 0 or snap > hb:
        raise RuntimeError(f"{label}: Phase242 snapshot is not before HB")
    fn = text.find("static void a52_r179_heartbeat_fn")
    end = text.find("static int __init a52_r179_early_heartbeat", fn)
    if fn < 0 or end < 0:
        raise RuntimeError(f"{label}: heartbeat function bounds missing")
    body = text[fn:end]
    if "a52_r241_corridor_replay(tick);" in body or 'CXF241 live t=%u' in body:
        raise RuntimeError(f"{label}: Phase241 bulk replay still live in heartbeat")


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
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def root_matches(root: Path) -> bool:
    path = root / RECORDER
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1" in text


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
        raise RuntimeError(f"expected exactly one generated Phase241 source root, found {len(unique)}: {rendered}")
    return unique[0]


def self_test() -> None:
    source = Path("/mnt/data/phase241-run7/stage/after/a52_ack_secure_flight_recorder.c")
    if source.is_file():
        patched = patch_recorder(source.read_text(encoding="utf-8"), "fixture/phase241-recorder")
        if patch_recorder(patched, "fixture/idempotent") != patched:
            raise AssertionError("Phase242 sticky overlay is not idempotent")
    else:
        fixture = (
            "\t * A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_IDENTITY_V1\n"
            "/* A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1 */\n"
            "static bool a52_r179_is_critical_message(const char *message) {\n"
            "return !strncmp(message, \"CXF241 \", 7) ||\n       false;\n}\n"
            "static void a52_r241_corridor_latch(const char *m) {}\n"
            "static void a52_r241_corridor_replay(unsigned int tick) {}\n"
            "void a52_ackfr_record(const char *fmt, ...)\n{\n"
            "if (strncmp(fmt, \"CXF241\", 6) &&\n    1) return;\n"
            "a52_r241_corridor_latch(event.message);\n}\n"
            "static void a52_r179_heartbeat_fn(struct work_struct *work)\n{\n"
            "\ta52_r228_tripost_snapshot(tick);\n"
            "\ta52_ackfr_record(\"HB tick=%u online=%u run=%lu j=%lu\", tick,\n"
            "\t\t 0, 0UL, 0UL);\n"
            + REPLAY_BLOCK +
            "}\nstatic int __init a52_r179_early_heartbeat(void) { return 0; }\n"
        )
        patched = patch_recorder(fixture, "fixture")
        validate(patched, "fixture/patched")
    print("Phase 242 sticky CX state overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    path = root / RECORDER
    before = path.read_text(encoding="utf-8")
    after = patch_recorder(before, str(path))
    path.write_text(after, encoding="utf-8")
    print("Phase 242 compact sticky CX state applied; Phase241 bulk replay disabled", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
