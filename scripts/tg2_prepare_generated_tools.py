#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

APPLY = Path("scripts/tg1_apply_critical_flight_recorder.py")
DECODE = Path("scripts/tg1_decode_critical_bank.py")
CI = Path("scripts/tg1_ci_build.sh")
V2_IMAGE_MARKER = "A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V2_EARLY_SEAL_90S"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def disable_v1_payload_rematerialization(ci: str) -> str:
    """Disable exactly one executable tg1_payload.py invocation in the v1 wrapper."""
    lines = ci.splitlines(keepends=True)
    hits: list[int] = []
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^\s*python3(?:\s|$)", line) and "tg1_payload.py" in line:
            hits.append(idx)

    if len(hits) != 1:
        candidates = [line.rstrip("\n") for line in lines if "tg1_payload.py" in line]
        raise SystemExit(
            "v1 wrapper payload rematerialization: expected one executable "
            f"python3 tg1_payload.py line, found {len(hits)}; candidates={candidates!r}"
        )

    idx = hits[0]
    original = lines[idx]
    newline = "\n" if original.endswith("\n") else ""
    indent = original[: len(original) - len(original.lstrip())]
    lines[idx] = (
        indent
        + ": # TouchGrass v2: payload already materialized and patched; "
        + "do not overwrite generated tools."
        + newline
    )
    return "".join(lines)


def main() -> int:
    apply = APPLY.read_text(encoding="utf-8")
    decode = DECODE.read_text(encoding="utf-8")
    ci = CI.read_text(encoding="utf-8")

    apply = replace_once(
        apply,
        '#define A52_TGCR_REASON_DSI_DMA_TIMEOUT   0x00000001U',
        '#define A52_TGCR_REASON_DSI_DMA_TIMEOUT   0x00000001U\n'
        '#define A52_TGCR_REASON_DEADLINE_90S      0x00000002U',
        'header fallback reason',
    )
    apply = replace_once(
        apply,
        ' * The DSI timeout path seals it exactly once. A sealed bank is never cleared\n'
        ' * or rewritten on the next boot, so watchdog reset cannot erase the evidence.\n',
        ' * The DSI timeout path seals it exactly once. If that path is never reached,\n'
        ' * a recorder-only 90-second fallback seal preserves the final circular window\n'
        ' * before the configured 100-second softdog. A sealed bank is never cleared or\n'
        ' * rewritten on the next boot, so watchdog reset cannot erase the evidence.\n',
        'recorder fallback comment',
    )

    init_anchor = '''static int __init a52_tgcr_init(void)
{
'''
    fallback_block = '''static void a52_tgcr_deadline_workfn(struct work_struct *work)
{
        (void)work;
        a52_tgcr_seal(A52_TGCR_REASON_DEADLINE_90S, 0xffffffffU,
                      0, 0, 0, 0, 0, 0, 0, 0, 0);
}

static DECLARE_DELAYED_WORK(a52_tgcr_deadline_work,
                            a52_tgcr_deadline_workfn);

static int __init a52_tgcr_init(void)
{
'''
    apply = replace_once(apply, init_anchor, fallback_block,
                         'deadline work insertion')

    arm_anchor = '''        atomic_set(&a52_tgcr_state, A52_TGCR_STATE_ARMED);
        pr_info("TouchGrass critical bank armed phys=%llx bytes=%lu slots=%u gen=%u\\n",
'''
    arm_repl = f'''        atomic_set(&a52_tgcr_state, A52_TGCR_STATE_ARMED);
        schedule_delayed_work(&a52_tgcr_deadline_work,
                              msecs_to_jiffies(90000));
        pr_info("{V2_IMAGE_MARKER}\\n");
        pr_info("TouchGrass critical bank armed phys=%llx bytes=%lu slots=%u gen=%u\\n",
'''
    apply = replace_once(apply, arm_anchor, arm_repl,
                         'deadline scheduling and v2 identity')

    decode = replace_once(
        decode,
        "REASONS = {1: 'DSI_DMA_TIMEOUT'}",
        "REASONS = {1: 'DSI_DMA_TIMEOUT', 2: 'DEADLINE_90S'}",
        'decoder reason map',
    )

    # tg1_ci_build.sh is itself materialized by tg1_payload.py. v2 has already
    # materialized that payload and patched APPLY/DECODE above. Running the v1
    # wrapper's own materializer again would overwrite those v2 changes and
    # compile a v1 Image. Disable exactly that redundant transport step only;
    # every v1 reconstruction, safety check, build, Image audit and repack step
    # remains unchanged.
    ci = disable_v1_payload_rematerialization(ci)

    APPLY.write_text(apply, encoding="utf-8")
    DECODE.write_text(decode, encoding="utf-8")
    CI.write_text(ci, encoding="utf-8")
    print("TouchGrass v2 generated-tool patch: PASS")
    print("TouchGrass v2 v1-wrapper rematerialization bypass: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
