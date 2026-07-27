#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

HEADER_REL = Path("include/linux/a52_ack_secure_flight_recorder.h")
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
WATCHDOG_REL = Path("drivers/watchdog/qcom-wdt.c")
REPORT = "phase25-a52-failure-window-probe-report.json"
MARKER = "A52_FAILURE_WINDOW_PROBE_V1"

GENERIC_TARGETS: dict[str, tuple[str, ...]] = {
    "drivers/gpu/drm/drm_panel.c": (
        "drm_panel_prepare",
        "drm_panel_enable",
        "drm_panel_disable",
        "drm_panel_unprepare",
    ),
    "drivers/gpu/drm/drm_atomic_helper.c": ("drm_atomic_helper_commit",),
    "drivers/video/backlight/backlight.c": ("backlight_update_status",),
    "drivers/video/fbdev/core/fbmem.c": ("fb_blank",),
}

VENDOR_TARGETS = (
    "sde_encoder_kickoff",
    "dsi_display_prepare",
    "dsi_display_enable",
    "dsi_display_disable",
    "dsi_display_unprepare",
    "dsi_panel_prepare",
    "dsi_panel_enable",
    "dsi_panel_disable",
    "dsi_panel_unprepare",
)

HEADER_BLOCK = r'''
/* A52_FAILURE_WINDOW_PROBE_V1 */
struct a52_ackfr_scope {
	const char *domain;
	const char *name;
	u64 start_ns;
};

struct a52_ackfr_scope a52_ackfr_scope_begin(const char *domain,
					      const char *name);
void a52_ackfr_scope_cleanup(struct a52_ackfr_scope *scope);

#define A52_ACKFR_SCOPE(domain, name) \
	struct a52_ackfr_scope __a52_ackfr_scope \
	__attribute__((cleanup(a52_ackfr_scope_cleanup))) = \
		a52_ackfr_scope_begin((domain), (name))
'''.strip()

SCOPE_SOURCE = r'''
/* A52_FAILURE_WINDOW_PROBE_V1: timed function scopes. */
struct a52_ackfr_scope a52_ackfr_scope_begin(const char *domain,
					      const char *name)
{
	struct a52_ackfr_scope scope = {
		.domain = domain,
		.name = name,
		.start_ns = ktime_get_ns(),
	};

	a52_ackfr_record("%s enter fn=%s", domain, name);
	return scope;
}
EXPORT_SYMBOL_GPL(a52_ackfr_scope_begin);

void a52_ackfr_scope_cleanup(struct a52_ackfr_scope *scope)
{
	u64 duration_us;

	if (!scope || !scope->start_ns)
		return;
	duration_us = div_u64(ktime_get_ns() - scope->start_ns, 1000U);
	a52_ackfr_record("%s exit fn=%s us=%llu", scope->domain, scope->name,
			  (unsigned long long)duration_us);
}
EXPORT_SYMBOL_GPL(a52_ackfr_scope_cleanup);

#define A52_USR2_HEARTBEAT_INTERVAL_MS 500U
#define A52_USR2_HEARTBEAT_LIMIT 600U

static atomic_t a52_usr2_heartbeat_count = ATOMIC_INIT(0);
static void a52_usr2_heartbeat_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_usr2_heartbeat_work,
			    a52_usr2_heartbeat_fn);

static void a52_usr2_heartbeat_fn(struct work_struct *work)
{
	unsigned int tick;

	tick = (unsigned int)atomic_inc_return(&a52_usr2_heartbeat_count);
	a52_ackfr_record("HB tick=%u online=%u run=%lu j=%lu", tick,
			  num_online_cpus(), nr_running(), jiffies);
	if (tick < A52_USR2_HEARTBEAT_LIMIT)
		schedule_delayed_work(&a52_usr2_heartbeat_work,
			msecs_to_jiffies(A52_USR2_HEARTBEAT_INTERVAL_MS));
}
'''.strip()

WATCHDOG_BLOCK = r'''
	/* A52_FAILURE_WINDOW_WATCHDOG_DISARM */
	{
		u32 a52_wdt_before;
		u32 a52_wdt_after;

		a52_wdt_before = readl(wdt_addr(wdt, WDT_STS));
		qcom_wdt_stop(&wdt->wdd);
		a52_wdt_after = readl(wdt_addr(wdt, WDT_STS));
		a52_ackfr_record("WDT disarm before=%u after=%u",
				  !!(a52_wdt_before & 1),
				  !!(a52_wdt_after & 1));
		dev_warn(&pdev->dev,
			 "A52 diagnostic: watchdog disabled for manual recovery\n");
		return 0;
	}
'''.strip("\n")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def add_include(path: Path, include_line: str) -> bool:
    text = read(path)
    if include_line in text:
        return False
    matches = list(re.finditer(r"^#include[^\n]*\n", text, flags=re.M))
    if matches:
        pos = matches[-1].end()
        text = text[:pos] + include_line + "\n" + text[pos:]
    else:
        text = include_line + "\n" + text
    write(path, text)
    return True


def mask_c(text: str) -> str:
    out = list(text)
    state = "normal"
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == "/" and nxt == "/":
                out[index] = out[index + 1] = " "
                state = "line-comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                out[index] = out[index + 1] = " "
                state = "block-comment"
                index += 2
                continue
            if char == '"':
                out[index] = " "
                state = "string"
                escaped = False
            elif char == "'":
                out[index] = " "
                state = "char"
                escaped = False
        elif state == "line-comment":
            if char == "\n":
                state = "normal"
            else:
                out[index] = " "
        elif state == "block-comment":
            if char == "*" and nxt == "/":
                out[index] = out[index + 1] = " "
                state = "normal"
                index += 2
                continue
            if char != "\n":
                out[index] = " "
        else:
            quote = '"' if state == "string" else "'"
            if char == "\n":
                escaped = False
            else:
                out[index] = " "
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    state = "normal"
        index += 1
    return "".join(out)


def top_level_before(masked: str, position: int) -> bool:
    depth = 0
    for char in masked[:position]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth == 0


def function_definition(text: str, name: str) -> tuple[int, int, int, str] | None:
    masked = mask_c(text)
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        if not top_level_before(masked, match.start()):
            continue
        paren = match.end() - 1
        depth = 0
        close_paren = -1
        for index in range(paren, len(masked)):
            char = masked[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_paren = index
                    break
        if close_paren < 0:
            continue
        tail = masked[close_paren + 1 : close_paren + 1024]
        brace_rel = tail.find("{")
        semi_rel = tail.find(";")
        if brace_rel < 0 or (semi_rel >= 0 and semi_rel < brace_rel):
            continue
        opening = close_paren + 1 + brace_rel
        brace_depth = 0
        for index in range(opening, len(masked)):
            if masked[index] == "{":
                brace_depth += 1
            elif masked[index] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    return line_start, opening, index, text[line_start : opening + 1]
    return None


def inject_scope(path: Path, name: str, domain: str = "DISP") -> str:
    text = read(path)
    marker = f'A52_ACKFR_SCOPE("{domain}", "{name}");'
    if marker in text:
        return "already-present"
    found = function_definition(text, name)
    if found is None:
        return "missing"
    _, opening, _, _ = found
    text = text[: opening + 1] + "\n\t" + marker + text[opening + 1 :]
    write(path, text)
    return "inserted"


def patch_header(path: Path) -> dict[str, object]:
    text = read(path)
    changed = False
    if "#include <linux/types.h>" not in text:
        anchor = "#include <linux/compiler.h>\n"
        if anchor not in text:
            raise SystemExit("recorder header compiler include anchor missing")
        text = text.replace(anchor, anchor + "#include <linux/types.h>\n", 1)
        changed = True
    if MARKER not in text:
        index = text.rfind("#endif")
        if index < 0:
            raise SystemExit("recorder header closing #endif missing")
        text = text[:index] + "\n" + HEADER_BLOCK + "\n\n" + text[index:]
        changed = True
    write(path, text)
    required = (
        MARKER,
        "struct a52_ackfr_scope",
        "A52_ACKFR_SCOPE(domain, name)",
        "a52_ackfr_scope_cleanup",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("recorder header probe audit failed: " + ", ".join(missing))
    return {"source": str(HEADER_REL), "changed": changed}


def patch_recorder(path: Path) -> dict[str, object]:
    text = read(path)
    original = text
    text, capacity_count = re.subn(
        r"#define A52_USR2_CAPACITY\s+\d+U",
        "#define A52_USR2_CAPACITY 1536U",
        text,
        count=1,
    )
    if capacity_count != 1:
        raise SystemExit("unified recorder capacity anchor missing")

    include_anchor = "#include <linux/utsname.h>\n"
    includes = (
        "#include <linux/cpu.h>\n",
        "#include <linux/jiffies.h>\n",
        "#include <linux/math64.h>\n",
        "#include <linux/sched/stat.h>\n",
        "#include <linux/workqueue.h>\n",
    )
    if include_anchor not in text:
        raise SystemExit("unified recorder include anchor missing")
    addition = "".join(item for item in includes if item not in text)
    if addition:
        text = text.replace(include_anchor, include_anchor + addition, 1)

    if "A52_USR2_HEARTBEAT_INTERVAL_MS" not in text:
        anchor = "EXPORT_SYMBOL_GPL(a52_ackfr_record);"
        if text.count(anchor) != 1:
            raise SystemExit("recorder export anchor count mismatch")
        text = text.replace(anchor, anchor + "\n\n" + SCOPE_SOURCE, 1)

    start_old = '\ta52_usr2_write_control("BOOT_READY");\n'
    start_new = (
        start_old
        + '\ta52_ackfr_record("HB start interval_ms=%u limit=%u",\n'
        + "\t\t\t  A52_USR2_HEARTBEAT_INTERVAL_MS,\n"
        + "\t\t\t  A52_USR2_HEARTBEAT_LIMIT);\n"
        + "\tschedule_delayed_work(&a52_usr2_heartbeat_work,\n"
        + "\t\tmsecs_to_jiffies(A52_USR2_HEARTBEAT_INTERVAL_MS));\n"
    )
    if "HB start interval_ms=%u limit=%u" not in text:
        count = text.count(start_old)
        if count != 1:
            raise SystemExit(f"heartbeat start anchor mismatch: expected 1, found {count}")
        text = text.replace(start_old, start_new, 1)

    if "A52_USR2_CONTINUOUS_PERSIST" not in text:
        declaration_old = "\tunsigned int written;\n\tva_list args;\n\tu64 seq;\n"
        declaration_new = (
            "\tunsigned int written;\n"
            "\tbool buffered;\n"
            "\tva_list args;\n"
            "\tu64 seq;\n"
        )
        if text.count(declaration_old) != 1:
            raise SystemExit("continuous persistence declaration anchor missing")
        text = text.replace(declaration_old, declaration_new, 1)

        limit_old = (
            "\tseq = (u64)atomic64_inc_return(&a52_usr2_sequence);\n"
            "\tif (seq > A52_USR2_CAPACITY) {\n"
            "\t\tatomic64_inc(&a52_usr2_dropped);\n"
            "\t\treturn;\n"
            "\t}\n"
        )
        limit_new = (
            "\tseq = (u64)atomic64_inc_return(&a52_usr2_sequence);\n"
            "\t/* A52_USR2_CONTINUOUS_PERSIST */\n"
            "\tbuffered = seq <= A52_USR2_CAPACITY;\n"
            "\tif (!buffered)\n"
            "\t\tatomic64_inc(&a52_usr2_dropped);\n"
        )
        if text.count(limit_old) != 1:
            raise SystemExit("continuous persistence limit anchor missing")
        text = text.replace(limit_old, limit_new, 1)

        store_old = (
            "\tspin_lock_irqsave(&a52_usr2_lock, irq_flags);\n"
            "\ta52_usr2_events[seq - 1] = event;\n"
            "\tspin_unlock_irqrestore(&a52_usr2_lock, irq_flags);\n"
        )
        store_new = (
            "\tif (buffered) {\n"
            "\t\tspin_lock_irqsave(&a52_usr2_lock, irq_flags);\n"
            "\t\ta52_usr2_events[seq - 1] = event;\n"
            "\t\tspin_unlock_irqrestore(&a52_usr2_lock, irq_flags);\n"
            "\t}\n"
        )
        if text.count(store_old) != 1:
            raise SystemExit("continuous persistence store anchor missing")
        text = text.replace(store_old, store_new, 1)

        mask_old = (
            "\tif (written) {\n"
            "\t\tspin_lock_irqsave(&a52_usr2_lock, irq_flags);\n"
            "\t\ta52_usr2_events[seq - 1].persisted_mask |= written;\n"
            "\t\tspin_unlock_irqrestore(&a52_usr2_lock, irq_flags);\n"
            "\t}\n"
        )
        mask_new = (
            "\tif (written && buffered) {\n"
            "\t\tspin_lock_irqsave(&a52_usr2_lock, irq_flags);\n"
            "\t\ta52_usr2_events[seq - 1].persisted_mask |= written;\n"
            "\t\tspin_unlock_irqrestore(&a52_usr2_lock, irq_flags);\n"
            "\t}\n"
        )
        if text.count(mask_old) != 1:
            raise SystemExit("continuous persistence mask anchor missing")
        text = text.replace(mask_old, mask_new, 1)

    write(path, text)
    required = (
        "#define A52_USR2_CAPACITY 1536U",
        "A52_USR2_HEARTBEAT_INTERVAL_MS 500U",
        "A52_USR2_HEARTBEAT_LIMIT 600U",
        "HB tick=%u online=%u run=%lu j=%lu",
        "a52_ackfr_scope_begin",
        "a52_ackfr_scope_cleanup",
        "schedule_delayed_work(&a52_usr2_heartbeat_work",
        "A52_USR2_CONTINUOUS_PERSIST",
    )
    final = read(path)
    missing = [item for item in required if item not in final]
    if missing:
        raise SystemExit("recorder failure-window audit failed: " + ", ".join(missing))
    return {
        "source": str(RECORDER_REL),
        "changed": final != original,
        "capacity": 1536,
        "heartbeat_interval_ms": 500,
        "heartbeat_limit": 600,
        "heartbeat_window_ms": 300000,
        "continuous_persistence_after_capacity": True,
    }


def patch_watchdog(path: Path) -> dict[str, object]:
    text = read(path)
    if "A52_FAILURE_WINDOW_WATCHDOG_DISARM" in text:
        return {"source": str(WATCHDOG_REL), "state": "already-present"}
    add_include(path, "#include <linux/a52_ack_secure_flight_recorder.h>")
    text = read(path)
    found = function_definition(text, "qcom_wdt_probe")
    if found is None:
        raise SystemExit("qcom_wdt_probe definition missing")
    _, opening, closing, _ = found
    body = text[opening + 1 : closing]
    match = re.search(
        r"(?m)^(?P<indent>[ \t]*)(?:ret\s*=\s*)?devm_watchdog_register_device\([^;]+\);",
        body,
    )
    if match is None:
        raise SystemExit("watchdog registration anchor missing")
    absolute = opening + 1 + match.start()
    indent = match.group("indent")
    block = "\n".join(indent + line if line else "" for line in WATCHDOG_BLOCK.splitlines())
    text = text[:absolute] + block + "\n" + text[absolute:]
    write(path, text)
    final = read(path)
    required = (
        "A52_FAILURE_WINDOW_WATCHDOG_DISARM",
        "qcom_wdt_stop(&wdt->wdd);",
        "WDT disarm before=%u after=%u",
        "watchdog disabled for manual recovery",
    )
    missing = [item for item in required if item not in final]
    if missing:
        raise SystemExit("watchdog disarm audit failed: " + ", ".join(missing))
    return {
        "source": str(WATCHDOG_REL),
        "state": "inserted",
        "registration_bypassed": True,
        "firmware_watchdog_disarmed": True,
    }


def patch_display(root: Path) -> dict[str, object]:
    results: list[dict[str, str]] = []
    changed_files: set[str] = set()

    for relative, names in GENERIC_TARGETS.items():
        path = root / relative
        if not path.is_file():
            for name in names:
                results.append({"path": relative, "function": name, "state": "missing-file"})
            continue
        for name in names:
            state = inject_scope(path, name)
            results.append({"path": relative, "function": name, "state": state})
            if state in {"inserted", "already-present"}:
                changed_files.add(relative)

    search_roots = [
        root / "drivers/gpu/drm",
        root / "techpack/display",
        root / "drivers/video",
    ]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        for path in search_root.rglob("*.c"):
            if path not in seen:
                seen.add(path)
                candidates.append(path)

    for name in VENDOR_TARGETS:
        matches: list[Path] = []
        for path in candidates:
            text = read(path)
            if name not in text:
                continue
            if function_definition(text, name) is not None:
                matches.append(path)
        if not matches:
            results.append({"path": "<vendor-search>", "function": name, "state": "missing"})
            continue
        for path in matches:
            state = inject_scope(path, name)
            relative = str(path.relative_to(root))
            results.append({"path": relative, "function": name, "state": state})
            if state in {"inserted", "already-present"}:
                changed_files.add(relative)

    include_line = "#include <linux/a52_ack_secure_flight_recorder.h>"
    for relative in sorted(changed_files):
        add_include(root / relative, include_line)

    active = [item for item in results if item["state"] in {"inserted", "already-present"}]
    active_names = {item["function"] for item in active}
    required = {"drm_panel_prepare", "drm_panel_enable", "drm_atomic_helper_commit"}
    missing_required = sorted(required - active_names)
    if missing_required:
        raise SystemExit(
            "required display probes missing: " + ", ".join(missing_required)
        )
    if len(active) < 6:
        raise SystemExit(f"too few active display probes: {len(active)}")
    return {
        "active_probe_count": len(active),
        "changed_files": sorted(changed_files),
        "required_functions": sorted(required),
        "results": results,
    }


def self_test() -> None:
    sample = """static int drm_panel_prepare(struct drm_panel *panel)\n{\n\treturn 0;\n}\n"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.c"
        path.write_text(sample, encoding="utf-8")
        first = inject_scope(path, "drm_panel_prepare")
        second = inject_scope(path, "drm_panel_prepare")
        text = read(path)
        if first != "inserted" or second != "already-present":
            raise SystemExit("display scope self-test failed")
        if 'A52_ACKFR_SCOPE("DISP", "drm_panel_prepare");' not in text:
            raise SystemExit("display scope marker missing after self-test")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    required = (root / HEADER_REL, root / RECORDER_REL, root / WATCHDOG_REL)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing failure-window sources: " + ", ".join(missing))

    report = {
        "status": "a52-failure-window-probe-v1-staged",
        "hardware_validated": False,
        "payload_capture": False,
        "header": patch_header(root / HEADER_REL),
        "recorder": patch_recorder(root / RECORDER_REL),
        "display": patch_display(root),
        "watchdog": patch_watchdog(root / WATCHDOG_REL),
        "diagnostic_questions": {
            "kernel_alive": "500 ms heartbeat continues",
            "scheduler_progress": "heartbeat nr_running and jiffies continue changing",
            "display_layer": "last DISP enter/exit pair identifies the final completed function",
            "latest_history": "events continue after the in-memory retry buffer fills; RAMOOPS keeps the newest window",
            "display_hang": "DISP enter without matching exit identifies the blocked call",
            "watchdog_reset": "QCOM watchdog is disarmed and not registered with userspace",
        },
        "scope": (
            "five-minute bounded heartbeat plus timed DRM, panel, DSI, SDE, backlight, "
            "and framebuffer function scopes, mirrored into RAMOOPS; Qualcomm watchdog "
            "is explicitly stopped so recovery can be entered manually"
        ),
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
