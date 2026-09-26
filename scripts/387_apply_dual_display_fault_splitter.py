#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
DRM = Path("drivers/gpu/drm/drm_atomic_helper.c")
MARK = "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase387 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"Phase387 {label}: function not found")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase387 {label}: opening brace not found")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise SystemExit(f"Phase387 {label}: closing brace not found")


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1" not in text:
        raise SystemExit("Phase387 DSI requires Phase293 lineage")

    # Exact-F0 path is one-shot, so these records cannot flood the ring. They
    # occur before Phase280 freezes the retained timeout snapshot.
    old = '''	/*
	 * This atomic state will be set if ISR has been triggered,
	 * so the wait is not needed.
	 */
	if (atomic_read(&dsi_ctrl->dma_irq_trig))
		goto done;
'''
    new = '''	/*
	 * This atomic state will be set if ISR has been triggered,
	 * so the wait is not needed.
	 */
	/* A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1 */
	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387D e trig=%d irqn=%d sw=%x ref=%u hw=%x",
			atomic_read(&dsi_ctrl->dma_irq_trig),
			dsi_ctrl->irq_info.irq_num,
			dsi_ctrl->irq_info.irq_stat_mask,
			dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE],
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
	if (atomic_read(&dsi_ctrl->dma_irq_trig)) {
		if (a52_p293_gdm_armed(dsi_ctrl))
			a52_ackfr_record("P276 387D short=1");
		goto done;
	}
'''
    text = one(text, old, new, "DMA wait entry")

    old = '''	ret = wait_for_completion_timeout(
			&dsi_ctrl->irq_info.cmd_dma_done,
			msecs_to_jiffies(DSI_CTRL_TX_TO_MS));
'''
    new = '''	ret = wait_for_completion_timeout(
			&dsi_ctrl->irq_info.cmd_dma_done,
			msecs_to_jiffies(DSI_CTRL_TX_TO_MS));
	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387D w ret=%d trig=%d hw=%x",
			ret, atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
'''
    text = one(text, old, new, "DMA wait call")

    old = '''		status = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);
'''
    new = '''		status = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);
		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_ackfr_record("P276 387D f st=%x done=%u hw=%x",
				status, !!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
			if (status & mask)
				a52_ackfr_record("P276 387D b=1 irq_lost=1");
			else
				a52_ackfr_record("P276 387D b=0 engine_done=0");
		}
'''
    text = one(text, old, new, "DMA fallback status read")

    # Record every ISR entry during the exact-F0 target, not only DMA_DONE.
    old = '''	/* clear interrupts */
	if (dsi_ctrl->hw.ops.clear_interrupt_status)
		dsi_ctrl->hw.ops.clear_interrupt_status(&dsi_ctrl->hw, 0x0);

	SDE_EVT32_IRQ(dsi_ctrl->cell_index, status, errors);
'''
    new = '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387I irq=%d st=%x raw=%x err=%llx",
			irq, status, DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			(unsigned long long)errors);

	/* clear interrupts */
	if (dsi_ctrl->hw.ops.clear_interrupt_status)
		dsi_ctrl->hw.ops.clear_interrupt_status(&dsi_ctrl->hw, 0x0);

	SDE_EVT32_IRQ(dsi_ctrl->cell_index, status, errors);
'''
    return one(text, old, new, "ISR entry")


def patch_drm(text: str) -> str:
    if MARK in text:
        return text

    # Keep the probe self-contained in common DRM code.
    anchor = "int\ndrm_atomic_helper_check_modeset(struct drm_device *dev,\n"
    start, end = function_bounds(text, anchor, "check_modeset")
    fn = text[start:end]

    helper = '''
/* A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1
 * Passive stage tagger for drm_atomic_helper_check_modeset(). It preserves
 * every original call, condition and return value.
 */
extern void a52_ackfr_record(const char *fmt, ...);
static atomic_t a52_p387_modeset_seq = ATOMIC_INIT(0);

static inline bool a52_p387_log(unsigned int n)
{
	return n <= 8U || !(n & 63U);
}

'''
    text = text[:start] + helper + text[start:]
    start += len(helper)
    end += len(helper)
    fn = text[start:end]

    fn = one(
        fn,
        "\tint i, ret;\n\tunsigned connectors_mask = 0;\n",
        "\tint i, ret;\n\tunsigned connectors_mask = 0;\n"
        "\tunsigned int a52_p387_n;\n\n"
        "\ta52_p387_n = (unsigned int)atomic_inc_return(&a52_p387_modeset_seq);\n",
        "modeset sequence",
    )

    fn = one(
        fn,
        '''		if (new_crtc_state->enable != has_connectors) {
			DRM_DEBUG_ATOMIC("[CRTC:%d:%s] enabled/connectors mismatch\\n",
					 crtc->base.id, crtc->name);

			return -EINVAL;
		}
''',
        '''		if (new_crtc_state->enable != has_connectors) {
			DRM_DEBUG_ATOMIC("[CRTC:%d:%s] enabled/connectors mismatch\\n",
					 crtc->base.id, crtc->name);
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=0 r=-22 crtc=%d en=%u cm=%x",
					a52_p387_n, crtc->base.id,
					new_crtc_state->enable,
					new_crtc_state->connector_mask);
			return -EINVAL;
		}
''',
        "enabled/connectors mismatch",
    )

    def stage(block: str, newblock: str, label: str) -> None:
        nonlocal fn
        fn = one(fn, block, newblock, label)

    stage(
        '''	ret = handle_conflicting_encoders(state, false);
	if (ret)
		return ret;
''',
        '''	ret = handle_conflicting_encoders(state, false);
	if (ret) {
		if (a52_p387_log(a52_p387_n))
			a52_ackfr_record("P276 387M n=%u st=1 r=%d", a52_p387_n, ret);
		return ret;
	}
''',
        "conflicting encoders",
    )

    stage(
        '''		ret = update_connector_routing(state, connector,
					       old_connector_state,
					       new_connector_state);
		if (ret)
			return ret;
''',
        '''		ret = update_connector_routing(state, connector,
					       old_connector_state,
					       new_connector_state);
		if (ret) {
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=2 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			return ret;
		}
''',
        "connector routing",
    )

    # Connector callback ABI differs across downstream/upstream revisions.
    for call in (
        "ret = funcs->atomic_check(connector, state);",
        "ret = funcs->atomic_check(connector, new_connector_state);",
    ):
        needle = "\t\tif (funcs->atomic_check)\n\t\t\t" + call + "\n\t\tif (ret)\n\t\t\treturn ret;\n"
        if fn.count(needle) == 2:
            first = (
                "\t\tif (funcs->atomic_check)\n\t\t\t" + call + "\n"
                "\t\tif (ret) {\n"
                "\t\t\tif (a52_p387_log(a52_p387_n))\n"
                "\t\t\t\ta52_ackfr_record(\"P276 387M n=%u st=3 r=%d conn=%d\",\n"
                "\t\t\t\t\ta52_p387_n, ret, connector->base.id);\n"
                "\t\t\treturn ret;\n\t\t}\n"
            )
            second = (
                "\t\tif (funcs->atomic_check)\n\t\t\t" + call + "\n"
                "\t\tif (ret) {\n"
                "\t\t\tif (a52_p387_log(a52_p387_n))\n"
                "\t\t\t\ta52_ackfr_record(\"P276 387M n=%u st=7 r=%d conn=%d\",\n"
                "\t\t\t\t\ta52_p387_n, ret, connector->base.id);\n"
                "\t\t\treturn ret;\n\t\t}\n"
            )
            fn = fn.replace(needle, first, 1).replace(needle, second, 1)
            break
    else:
        raise SystemExit("Phase387 connector atomic_check ABI anchor missing")

    stage(
        '''		ret = drm_atomic_add_affected_connectors(state, crtc);
		if (ret != 0)
			return ret;

		ret = drm_atomic_add_affected_planes(state, crtc);
		if (ret != 0)
			return ret;
''',
        '''		ret = drm_atomic_add_affected_connectors(state, crtc);
		if (ret != 0) {
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=4 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			return ret;
		}

		ret = drm_atomic_add_affected_planes(state, crtc);
		if (ret != 0) {
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=5 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			return ret;
		}
''',
        "affected objects",
    )

    clone = '''		ret = drm_atomic_check_valid_clones(state, crtc);
		if (ret != 0)
			return ret;
'''
    if clone in fn:
        fn = one(
            fn, clone,
            '''		ret = drm_atomic_check_valid_clones(state, crtc);
		if (ret != 0) {
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=6 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			return ret;
		}
''',
            "valid clones",
        )

    # Newer 5.10 adds encoder bridges; tag these if present.
    bridge_old = '''		encoder = old_connector_state->best_encoder;
		ret = drm_atomic_add_encoder_bridges(state, encoder);
		if (ret)
			return ret;

		encoder = new_connector_state->best_encoder;
		ret = drm_atomic_add_encoder_bridges(state, encoder);
		if (ret)
			return ret;
'''
    if bridge_old in fn:
        fn = one(
            fn, bridge_old,
            '''		encoder = old_connector_state->best_encoder;
		ret = drm_atomic_add_encoder_bridges(state, encoder);
		if (ret) {
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=8 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			return ret;
		}

		encoder = new_connector_state->best_encoder;
		ret = drm_atomic_add_encoder_bridges(state, encoder);
		if (ret) {
			if (a52_p387_log(a52_p387_n))
				a52_ackfr_record("P276 387M n=%u st=9 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			return ret;
		}
''',
            "encoder bridges",
        )

    stage(
        '''	ret = mode_valid(state);
	if (ret)
		return ret;

	return mode_fixup(state);
''',
        '''	ret = mode_valid(state);
	if (ret) {
		if (a52_p387_log(a52_p387_n))
			a52_ackfr_record("P276 387M n=%u st=10 r=%d", a52_p387_n, ret);
		return ret;
	}

	ret = mode_fixup(state);
	if (ret && a52_p387_log(a52_p387_n))
		a52_ackfr_record("P276 387M n=%u st=11 r=%d", a52_p387_n, ret);
	return ret;
''',
        "mode valid/fixup",
    )

    return text[:start] + fn + text[end:]


def validate(root: Path) -> None:
    dsi = (root / DSI).read_text()
    drm = (root / DRM).read_text()
    for token in (
        MARK,
        "P276 387D e trig=%d irqn=%d sw=%x ref=%u hw=%x",
        "P276 387D w ret=%d trig=%d hw=%x",
        "P276 387D f st=%x done=%u hw=%x",
        "P276 387D b=1 irq_lost=1",
        "P276 387D b=0 engine_done=0",
        "P276 387I irq=%d st=%x raw=%x err=%llx",
    ):
        if token not in dsi:
            raise SystemExit("Phase387 DSI token missing: " + token)
    for token in (
        MARK,
        "a52_p387_modeset_seq",
        "P276 387M n=%u st=0 r=-22",
        "P276 387M n=%u st=1 r=%d",
        "P276 387M n=%u st=2 r=%d",
        "P276 387M n=%u st=3 r=%d",
        "P276 387M n=%u st=4 r=%d",
        "P276 387M n=%u st=5 r=%d",
        "P276 387M n=%u st=7 r=%d",
        "P276 387M n=%u st=10 r=%d",
        "P276 387M n=%u st=11 r=%d",
    ):
        if token not in drm:
            raise SystemExit("Phase387 DRM token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root
    for p in (DSI, DRM):
        if not (root / p).is_file():
            raise SystemExit("Phase387 missing source: " + str(p))
    if not ns.check_only:
        (root / DSI).write_text(patch_dsi((root / DSI).read_text()))
        (root / DRM).write_text(patch_drm((root / DRM).read_text()))
    validate(root)
    print("Phase387 dual display fault splitter: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
