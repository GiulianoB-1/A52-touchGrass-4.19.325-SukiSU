#!/usr/bin/env python3
"""Run Phase 226, retain ODSPOST, then add Phase 228 tri-track snapshots."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASE_MARKER = "A52_PHASE226_ODSIGN_GATE_TRACE"
PHASE228_MARKER = "A52_PHASE228_TRI_TRACK_SNAPSHOT"
ODS_ALLOW = '!strncmp(message, "ODSPOST ", 8)'
TRI_ALLOW = '!strncmp(message, "TRIPOST ", 8)'
RELATIVE = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")

BOOTPOST_ANCHOR_RE = re.compile(
    r'(?m)^(?P<indent>[ \t]*)!strncmp\(message, "BOOTPOST ", 9\) \|\|$'
)
WRITE_CONTROL_ANCHOR = "static void a52_r179_write_control(const char *kind)\n"
UPDATE_ANCHOR = "\tva_end(args);\n\tcritical = a52_r179_is_critical_message(event.message);\n"
HEARTBEAT_ANCHOR = (
    "\ttick = (unsigned int)atomic_inc_return(&a52_r179_heartbeat_count);\n"
    "\ta52_r226_task_snapshot(tick);\n"
    "\ta52_ackfr_record(\"HB tick=%u online=%u run=%lu j=%lu\", tick,\n"
)

TRI_BLOCK = r'''/* A52_PHASE228_TRI_TRACK_SNAPSHOT
 * Compact cumulative state survives ring overwrite and correlates the early
 * vold boundary, the odsign/odrefresh boundary, and the late SF/KGSL boundary.
 * Observation only: no return value, ordering, signal, or security behavior is
 * changed.
 */
static atomic_t a52_r228_v_stage = ATOMIC_INIT(0);
static atomic_t a52_r228_v_value = ATOMIC_INIT(0);
static atomic_t a52_r228_v_count = ATOMIC_INIT(0);
static atomic_t a52_r228_o_proc = ATOMIC_INIT(0);
static atomic_t a52_r228_o_stage = ATOMIC_INIT(0);
static atomic_t a52_r228_o_value = ATOMIC_INIT(0);
static atomic_t a52_r228_o_count = ATOMIC_INIT(0);
static atomic_t a52_r228_f_stage = ATOMIC_INIT(0);
static atomic_t a52_r228_f_value = ATOMIC_INIT(0);
static atomic_t a52_r228_f_count = ATOMIC_INIT(0);
static atomic_t a52_r228_g_open = ATOMIC_INIT(-61);
static atomic_t a52_r228_g_probe = ATOMIC_INIT(0);
static atomic_t a52_r228_g_reg = ATOMIC_INIT(0);
static atomic_t a52_r228_g_node = ATOMIC_INIT(0);

static bool a52_r228_has(const char *message, const char *needle)
{
	return message && needle && strnstr(message, needle, A52_R179_MESSAGE_LEN);
}

static int a52_r228_dec(const char *message, const char *key, int fallback)
{
	const char *value;
	int parsed;

	value = strnstr(message, key, A52_R179_MESSAGE_LEN);
	if (!value)
		return fallback;
	value += strlen(key);
	return sscanf(value, "%d", &parsed) == 1 ? parsed : fallback;
}

static int a52_r228_hex(const char *message, const char *key, int fallback)
{
	const char *value;
	unsigned int parsed;

	value = strnstr(message, key, A52_R179_MESSAGE_LEN);
	if (!value)
		return fallback;
	value += strlen(key);
	return sscanf(value, "%x", &parsed) == 1 ? (int)parsed : fallback;
}

static int a52_r228_stage(const char *message)
{
	if (a52_r228_has(message, "exec-ret"))
		return 2;
	if (a52_r228_has(message, "exec "))
		return 1;
	if (a52_r228_has(message, "exit"))
		return 3;
	if (a52_r228_has(message, "open"))
		return 4;
	if (a52_r228_has(message, "io-in"))
		return 5;
	if (a52_r228_has(message, "io-out"))
		return 6;
	if (a52_r228_has(message, "con-in"))
		return 7;
	if (a52_r228_has(message, "con-out"))
		return 8;
	return 0;
}

static int a52_r228_value(const char *message, int fallback)
{
	int value;

	value = a52_r228_dec(message, "code=", fallback);
	if (value != fallback)
		return value;
	value = a52_r228_dec(message, "rc=", fallback);
	if (value != fallback)
		return value;
	value = a52_r228_dec(message, "r=", fallback);
	if (value != fallback)
		return value;
	return a52_r228_hex(message, "x=", fallback);
}

static void a52_r228_track_message(const char *message)
{
	int stage;
	int value;

	if (!message || !strncmp(message, "TRIPOST ", 8))
		return;

	if (a52_r228_has(message, "vold")) {
		stage = a52_r228_stage(message);
		value = a52_r228_value(message, atomic_read(&a52_r228_v_value));
		if (stage)
			atomic_set(&a52_r228_v_stage, stage);
		atomic_set(&a52_r228_v_value, value);
		atomic_inc(&a52_r228_v_count);
	}

	if (strncmp(message, "ODSPOST ", 8) == 0) {
		stage = a52_r228_stage(message);
		value = a52_r228_value(message, atomic_read(&a52_r228_o_value));
		if (a52_r228_has(message, "odsign"))
			atomic_set(&a52_r228_o_proc, 1);
		else if (a52_r228_has(message, "odrefresh"))
			atomic_set(&a52_r228_o_proc, 2);
		if (stage)
			atomic_set(&a52_r228_o_stage, stage);
		atomic_set(&a52_r228_o_value, value);
		atomic_inc(&a52_r228_o_count);
	}

	if (!strncmp(message, "BOOTPOST ", 9) &&
	    a52_r228_has(message, "surfaceflinger")) {
		stage = a52_r228_stage(message);
		value = a52_r228_value(message, atomic_read(&a52_r228_f_value));
		if (stage)
			atomic_set(&a52_r228_f_stage, stage);
		atomic_set(&a52_r228_f_value, value);
		if (stage == 1)
			atomic_inc(&a52_r228_f_count);
	}

	if (a52_r228_has(message, "GFXPOST 225 ks1")) {
		atomic_set(&a52_r228_g_open,
			a52_r228_dec(message, " o=", atomic_read(&a52_r228_g_open)));
		atomic_set(&a52_r228_g_probe,
			a52_r228_dec(message, " ps=", atomic_read(&a52_r228_g_probe)));
	}
	if (a52_r228_has(message, "GFXPOST 225 ks2")) {
		atomic_set(&a52_r228_g_reg,
			a52_r228_dec(message, " rs=", atomic_read(&a52_r228_g_reg)));
		atomic_set(&a52_r228_g_node,
			a52_r228_dec(message, " ns=", atomic_read(&a52_r228_g_node)));
	}
}

static int a52_r228_clip(int value)
{
	if (value > 999)
		return 999;
	if (value < -999)
		return -999;
	return value;
}

static void a52_r228_tripost_snapshot(unsigned int tick)
{
	if (tick != 1U && (tick % 2U))
		return;

	a52_ackfr_record("TRIPOST 228 t=%u v=%d,%d,%d o=%d,%d,%d,%d f=%d,%d,%d g=%d,%d,%d,%d",
		tick,
		a52_r228_clip(atomic_read(&a52_r228_v_stage)),
		a52_r228_clip(atomic_read(&a52_r228_v_value)),
		a52_r228_clip(atomic_read(&a52_r228_v_count)),
		a52_r228_clip(atomic_read(&a52_r228_o_proc)),
		a52_r228_clip(atomic_read(&a52_r228_o_stage)),
		a52_r228_clip(atomic_read(&a52_r228_o_value)),
		a52_r228_clip(atomic_read(&a52_r228_o_count)),
		a52_r228_clip(atomic_read(&a52_r228_f_stage)),
		a52_r228_clip(atomic_read(&a52_r228_f_value)),
		a52_r228_clip(atomic_read(&a52_r228_f_count)),
		a52_r228_clip(atomic_read(&a52_r228_g_open)),
		a52_r228_clip(atomic_read(&a52_r228_g_probe)),
		a52_r228_clip(atomic_read(&a52_r228_g_reg)),
		a52_r228_clip(atomic_read(&a52_r228_g_node)));
}

'''


def allow_count(text: str, token: str) -> int:
    pattern = re.compile(r"(?m)^[ \t]*" + re.escape(token) + r" \|\|$")
    return len(pattern.findall(text))


def add_allow(text: str, token: str, *, label: str) -> str:
    count = allow_count(text, token)
    if count == 1:
        return text
    if count != 0:
        raise RuntimeError(f"{label}: unexpected allowlist count for {token}: {count}")
    matches = list(BOOTPOST_ANCHOR_RE.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{label}: expected one BOOTPOST anchor, found {len(matches)}")
    match = matches[0]
    insertion = match.group(0) + "\n" + match.group("indent") + token + " ||"
    return text[:match.start()] + insertion + text[match.end():]


def patch_text(text: str, *, label: str) -> str:
    if BASE_MARKER not in text:
        raise RuntimeError(f"{label}: missing Phase 226 recorder marker")

    text = add_allow(text, ODS_ALLOW, label=label)
    text = add_allow(text, TRI_ALLOW, label=label)

    if PHASE228_MARKER not in text:
        if text.count(WRITE_CONTROL_ANCHOR) != 1:
            raise RuntimeError(f"{label}: write-control anchor mismatch")
        text = text.replace(WRITE_CONTROL_ANCHOR, TRI_BLOCK + WRITE_CONTROL_ANCHOR)

        if text.count(UPDATE_ANCHOR) != 1:
            raise RuntimeError(f"{label}: record update anchor mismatch")
        text = text.replace(
            UPDATE_ANCHOR,
            "\tva_end(args);\n\ta52_r228_track_message(event.message);\n"
            "\tcritical = a52_r179_is_critical_message(event.message);\n",
        )

        if text.count(HEARTBEAT_ANCHOR) != 1:
            raise RuntimeError(f"{label}: heartbeat anchor mismatch")
        text = text.replace(
            HEARTBEAT_ANCHOR,
            "\ttick = (unsigned int)atomic_inc_return(&a52_r179_heartbeat_count);\n"
            "\ta52_r226_task_snapshot(tick);\n"
            "\ta52_r228_tripost_snapshot(tick);\n"
            "\ta52_ackfr_record(\"HB tick=%u online=%u run=%lu j=%lu\", tick,\n",
        )

    if allow_count(text, ODS_ALLOW) != 1 or allow_count(text, TRI_ALLOW) != 1:
        raise RuntimeError(f"{label}: final critical allowlist audit failed")
    checks = {
        PHASE228_MARKER: 1,
        "a52_r228_track_message(event.message);": 1,
        "a52_r228_tripost_snapshot(tick);": 1,
        'a52_ackfr_record("TRIPOST 228 ': 1,
    }
    for token, expected in checks.items():
        count = text.count(token)
        if count != expected:
            raise RuntimeError(f"{label}: expected {expected} occurrence(s) of {token!r}, found {count}")
    return text


def candidate_sources(arguments: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        root = Path(value)
        candidates.extend((root / RELATIVE, root / "a52_ack_secure_flight_recorder.c"))
    candidates.extend(
        (
            Path("workspace/gki-phase199-src") / RELATIVE,
            Path("gki/common") / RELATIVE,
        )
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def patch_generated_source(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for candidate in candidate_sources(arguments):
        if candidate.is_file() and BASE_MARKER in candidate.read_text(encoding="utf-8"):
            matches.append(candidate)
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(f"expected one generated Phase 226 recorder source, found {len(matches)}: {rendered}")
    target = matches[0]
    target.write_text(patch_text(target.read_text(encoding="utf-8"), label=str(target)), encoding="utf-8")
    return target


def self_test() -> None:
    fixture = (
        "/* A52_PHASE226_ODSIGN_GATE_TRACE */\n"
        "static bool a52_r179_is_critical_message(const char *message)\n"
        "{\n"
        "\treturn !strncmp(message, \"GFXPOST \", 8) ||\n"
        "\t       !strncmp(message, \"BOOTPOST \", 9) ||\n"
        "\t       !strncmp(message, \"KMSPOST \", 8);\n"
        "}\n\n"
        "static void a52_r179_write_control(const char *kind)\n"
        "{\n\t(void)kind;\n}\n\n"
        "void a52_ackfr_record(const char *fmt, ...)\n"
        "{\n"
        "\tstruct a52_r179_event event;\n"
        "\tbool critical;\n"
        "\tva_list args;\n"
        "\tva_start(args, fmt);\n"
        "\tvscnprintf(event.message, sizeof(event.message), fmt, args);\n"
        "\tva_end(args);\n"
        "\tcritical = a52_r179_is_critical_message(event.message);\n"
        "\t(void)critical;\n"
        "}\n\n"
        "static void a52_r179_heartbeat_fn(struct work_struct *work)\n"
        "{\n"
        "\tunsigned int tick;\n"
        "\ttick = (unsigned int)atomic_inc_return(&a52_r179_heartbeat_count);\n"
        "\ta52_r226_task_snapshot(tick);\n"
        "\ta52_ackfr_record(\"HB tick=%u online=%u run=%lu j=%lu\", tick,\n"
        "\t\t\t  num_online_cpus(), nr_running(), jiffies);\n"
        "}\n"
    )
    patched = patch_text(fixture, label="self-test")
    if patch_text(patched, label="self-test-idempotent") != patched:
        raise AssertionError("Phase 228 patch is not idempotent")
    marker = 'a52_ackfr_record("TRIPOST 228 t=%u v=%d,%d,%d o=%d,%d,%d,%d f=%d,%d,%d g=%d,%d,%d,%d"'
    if marker not in patched:
        raise AssertionError("Phase 228 compact snapshot format missing")
    print("Phase 228 tri-track cumulative snapshot self-test: PASS")


def main() -> int:
    base = Path(__file__).with_name("218_phase217_wrapper_phase226.py")
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    if not base.is_file():
        raise SystemExit(f"missing Phase 226 base wrapper: {base}")
    completed = subprocess.run([sys.executable, str(base), *sys.argv[1:]], check=False)
    if completed.returncode:
        return completed.returncode
    target = patch_generated_source(sys.argv[1:])
    print(f"Phase 228 tri-track cumulative snapshot applied to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
