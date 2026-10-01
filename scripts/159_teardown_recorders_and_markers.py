#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_P159_FINAL_DIAGNOSTIC_TEARDOWN_V1"

RECORDER_TOKENS = (
    "a52_p155_",
    "A52_P155_",
    "mglru_rec_",
    "mglru_diag_",
    "mglru_record_level",
    "a52_sched_diag_",
    "A52_R48",
    "R48_REC",
)

LOG_MARKER_TOKENS = (
    "A52_BOOT_RESCUE:",
    "[AOD P150]",
    "FUSE_740_",
    "P155 ",
    "A52 P1",
    "A52_P1",
)

CALL_NAMES = (
    "pr_info", "pr_info_once", "pr_warn", "pr_err", "pr_emerg",
    "pr_debug", "dev_info", "dev_warn", "dev_err", "dev_dbg",
    "LCD_INFO", "LCD_DEBUG", "LCD_ERR",
)

TEXT_SUFFIXES = {".c", ".h", ".S", ".s", ".lds", ".dtsi", ".dts"}

def remove_call_statements_containing(text: str, func: str, needles: tuple[str, ...]):
    pos = 0
    removed = 0
    while True:
        start = text.find(func + "(", pos)
        if start < 0:
            break
        depth = 0
        i = start + len(func)
        in_str = False
        esc = False
        end = None
        while i < len(text):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        j = i + 1
                        while j < len(text) and text[j] in " \t":
                            j += 1
                        if j < len(text) and text[j] == ";":
                            end = j + 1
                        break
            i += 1
        if end is None:
            pos = start + len(func) + 1
            continue
        stmt = text[start:end]
        if any(n in stmt for n in needles):
            line_start = text.rfind("\n", 0, start) + 1
            # Only remove the whole line when the call is the statement,
            # otherwise replace just the call statement.
            prefix = text[line_start:start]
            if prefix.strip() == "":
                line_end = end
                while line_end < len(text) and text[line_end] in " \t":
                    line_end += 1
                if line_end < len(text) and text[line_end] == "\n":
                    line_end += 1
                text = text[:line_start] + text[line_end:]
                pos = line_start
            else:
                text = text[:start] + text[end:]
                pos = start
            removed += 1
        else:
            pos = end
    return text, removed

def strip_phase_comments(text: str):
    removed = 0

    # Block comments containing our project phase/diagnostic markers.
    block = re.compile(r"/\*(?:(?!\*/).)*(?:A52|A619)(?:(?!\*/).)*\*/", re.S)
    def repl_block(m):
        nonlocal removed
        body = m.group(0)
        if re.search(r"\bP(?:1[0-5][0-9]|[1-9][0-9]?)\b|BOOT RESCUE|MGLRU|SCHEDUTIL|UCLAMP|KGSL|BATTERY", body):
            removed += 1
            return ""
        return body
    text = block.sub(repl_block, text)

    # Single-line C++ comments carrying the same phase markers.
    line = re.compile(r"(?m)^[ \t]*//[^\n]*(?:A52|A619)[^\n]*(?:P(?:1[0-5][0-9]|[1-9][0-9]?)|BOOT RESCUE|MGLRU|SCHEDUTIL|UCLAMP|KGSL|BATTERY)[^\n]*\n?")
    text, n = line.subn("", text)
    removed += n
    return text, removed

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ns = ap.parse_args()
    root = ns.root.resolve()
    report = ns.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)

    changed_files = 0
    comments_removed = 0
    logs_removed = 0
    removed_files = []

    # Defensive teardown: P159 production must not carry P155 at all.
    for rel in (
        "drivers/misc/a52_p155_esd_recorder.c",
        "include/linux/a52_p155_esd_recorder.h",
    ):
        p = root / rel
        if p.exists():
            p.unlink()
            removed_files.append(rel)

    misc_mk = root / "drivers/misc/Makefile"
    if misc_mk.is_file():
        old = misc_mk.read_text()
        new = re.sub(r"(?m)^obj-y\s*\+=\s*a52_p155_esd_recorder\.o\s*\n?", "", old)
        if new != old:
            misc_mk.write_text(new)
            changed_files += 1

    # Remove only log calls carrying our diagnostic/phase marker strings.
    # Functional code around those calls is preserved.
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            old = p.read_text()
        except UnicodeDecodeError:
            continue
        new = old
        for fn in CALL_NAMES:
            new, n = remove_call_statements_containing(new, fn, LOG_MARKER_TOKENS)
            logs_removed += n
        new, n = strip_phase_comments(new)
        comments_removed += n
        if new != old:
            p.write_text(new)
            changed_files += 1

    # Hard fail if recorder implementation/symbols survive.
    offenders = []
    marker_logs = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        for tok in RECORDER_TOKENS:
            if tok in text:
                offenders.append(f"{p.relative_to(root)}:{tok}")
        for tok in LOG_MARKER_TOKENS:
            if tok in text and any(fn + "(" in text for fn in CALL_NAMES):
                # This is deliberately conservative: report only files that
                # still contain a marker token and logging APIs.
                marker_logs.append(f"{p.relative_to(root)}:{tok}")

    if offenders:
        raise SystemExit("recorder token(s) remain:\n" + "\n".join(sorted(set(offenders))[:100]))
    if marker_logs:
        raise SystemExit("diagnostic marker log(s) remain:\n" + "\n".join(sorted(set(marker_logs))[:100]))

    report.write_text(
        f"{MARK}\n"
        f"changed_files={changed_files}\n"
        f"removed_files={len(removed_files)}\n"
        f"phase_comments_removed={comments_removed}\n"
        f"diagnostic_log_calls_removed={logs_removed}\n"
        + "".join(f"removed_file={x}\n" for x in removed_files)
    )
    print(report.read_text(), end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
