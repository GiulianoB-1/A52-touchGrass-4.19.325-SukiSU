#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

RECORDER_REL = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
ATOMIC_REL = Path('drivers/a52_display/msm/msm_atomic.c')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def replace_once_after(text: str, start_marker: str, old: str, new: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f'{label}: start marker missing')
    pos = text.find(old, start)
    if pos < 0:
        raise RuntimeError(f'{label}: anchor missing after start marker')
    return text[:pos] + new + text[pos + len(old):]


def patch_recorder(text: str) -> str:
    replacements = [
        ('A52 GKI 5.10 display takeover recorder, phase 199.',
         'A52 GKI 5.10 first-atomic recorder, phase 210.'),
        ('#define pr_fmt(fmt) "A52R199: " fmt',
         '#define pr_fmt(fmt) "A52R210: " fmt'),
        ('#define A52_R179_COMMIT 0x5a52c199U',
         '#define A52_R179_COMMIT 0x5a52c210U'),
        ('#define A52_R179_VERSION 2U',
         '#define A52_R179_VERSION 3U'),
        ('#define A52_R179_RS_ROOTS 32U',
         '#define A52_R179_RS_ROOTS 48U'),
        ('#define A52_R179_DATA_BYTES 157U',
         '#define A52_R179_DATA_BYTES 141U'),
        ('#define A52_R179_PREFIX "R99"',
         '#define A52_R179_PREFIX "R48"'),
        ('#define A52_R179_PREFIX_BYTES 3U',
         '#define A52_R179_PREFIX_BYTES 3U\n#define A52_R210_PACKED_MESSAGE_LEN 73U'),
        ('\tchar message[A52_R179_MESSAGE_LEN - 1];',
         '\tchar message[A52_R210_PACKED_MESSAGE_LEN];'),
        ('\tmemcpy(data->magic, "A52R0199", sizeof(data->magic));',
         '\tmemcpy(data->magic, "A52R0210", sizeof(data->magic));'),
        ('a52_ackfr_record("BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c",',
         'a52_ackfr_record("BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c",'),
    ]
    for old, new in replacements:
        text = replace_once(text, old, new, old)
    required = [
        '#define A52_R179_BANK_CONSOLE BIT(0)',
        '#define A52_R179_BANK_FTRACE BIT(1)',
        '#define A52_R179_BANK_RECORD BIT(2)',
        'a52_r179_persist_event(&event, A52_R179_BANK_ALL)',
        '!strncmp(message, "DRMPOST ", 8)',
        '__le32 crc32c;',
    ]
    for marker in required:
        if marker not in text:
            raise RuntimeError(f'recorder invariant missing: {marker}')
    return text


def patch_atomic(text: str) -> str:
    text = replace_once(
        text,
        '#include "sde_trace.h"\n',
        '#include "sde_trace.h"\n#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'atomic includes',
    )
    text = replace_once(
        text,
        '#define MULTIPLE_CONN_DETECTED(x) (x > 1)\n',
        '''#define MULTIPLE_CONN_DETECTED(x) (x > 1)\n\n#define A52_R210_TRACE_LIMIT 8\n\nstatic atomic_t a52_r210_commit_sequence = ATOMIC_INIT(0);\n\n#define A52_R210_REC_ID(enabled, id, fmt, ...) \\\n\tdo { \\\n\t\tif (enabled) \\\n\t\t\ta52_ackfr_record("DRMPOST 210 c=%u " fmt, \\\n\t\t\t\t\t  (unsigned int)(id), ##__VA_ARGS__); \\\n\t} while (0)\n\n#define A52_R210_REC(commit, fmt, ...) \\\n\tA52_R210_REC_ID((commit)->a52_trace, (commit)->a52_trace_id, \\\n\t\t\t  fmt, ##__VA_ARGS__)\n''',
        'trace macro block',
    )
    text = replace_once(
        text,
        '\tbool nonblock;\n\tstruct kthread_work commit_work;\n',
        '\tbool nonblock;\n\tbool a52_trace;\n\tu32 a52_trace_id;\n\tstruct kthread_work commit_work;\n',
        'commit trace fields',
    )

    text = replace_once(
        text,
        '#endif\n\n\tdrm_atomic_helper_wait_for_fences(dev, state, false);\n\n\tkms->funcs->prepare_commit(kms, state);\n\n\tmsm_atomic_helper_commit_modeset_disables(dev, state);\n\n\tdrm_atomic_helper_commit_planes(dev, state,\n\t\t\t\tDRM_PLANE_COMMIT_ACTIVE_ONLY);\n\n\tmsm_atomic_helper_commit_modeset_enables(dev, state);\n',
        '''#endif\n\n\tA52_R210_REC(c, "complete enter");\n\tA52_R210_REC(c, "fences wait enter");\n\tdrm_atomic_helper_wait_for_fences(dev, state, false);\n\tA52_R210_REC(c, "fences wait exit");\n\n\tA52_R210_REC(c, "prepare_commit enter");\n\tkms->funcs->prepare_commit(kms, state);\n\tA52_R210_REC(c, "prepare_commit exit");\n\n\tA52_R210_REC(c, "modeset_disable enter");\n\tmsm_atomic_helper_commit_modeset_disables(dev, state);\n\tA52_R210_REC(c, "modeset_disable exit");\n\n\tA52_R210_REC(c, "planes enter");\n\tdrm_atomic_helper_commit_planes(dev, state,\n\t\t\t\tDRM_PLANE_COMMIT_ACTIVE_ONLY);\n\tA52_R210_REC(c, "planes exit");\n\n\tA52_R210_REC(c, "modeset_enable enter");\n\tmsm_atomic_helper_commit_modeset_enables(dev, state);\n\tA52_R210_REC(c, "modeset_enable exit");\n''',
        'complete commit early stages',
    )
    old_wait = '\tmsm_atomic_wait_for_commit_done(dev, state);\n'
    if text.count(old_wait) < 1:
        raise RuntimeError('wait commit done anchor missing')
    text = text.replace(old_wait, '\tA52_R210_REC(c, "wait_done enter");\n\tmsm_atomic_wait_for_commit_done(dev, state);\n\tA52_R210_REC(c, "wait_done exit");\n', 1)
    text = replace_once(
        text,
        '\tdrm_atomic_helper_cleanup_planes(dev, state);\n\n\tkms->funcs->complete_commit(kms, state);\n\n\tdrm_atomic_state_put(state);\n\n\tcommit_destroy(c);\n',
        '''\tA52_R210_REC(c, "cleanup enter");\n\tdrm_atomic_helper_cleanup_planes(dev, state);\n\tA52_R210_REC(c, "cleanup exit");\n\n\tA52_R210_REC(c, "complete_cb enter");\n\tkms->funcs->complete_commit(kms, state);\n\tA52_R210_REC(c, "complete_cb exit");\n\n\tdrm_atomic_state_put(state);\n\n\tA52_R210_REC(c, "complete exit");\n\tcommit_destroy(c);\n''',
        'complete commit tail',
    )

    text = replace_once(
        text,
        '\tint ret = -ECANCELED, i = 0, j = 0;\n\tbool nonblock;\n\n\t/* cache since work will kfree commit in non-blocking case */\n\tnonblock = commit->nonblock;\n',
        '''\tint ret = -ECANCELED, i = 0, j = 0;\n\tbool nonblock;\n\tbool a52_trace;\n\tu32 a52_trace_id;\n\n\t/* cache since work may free commit in the non-blocking case */\n\tnonblock = commit->nonblock;\n\ta52_trace = commit->a52_trace;\n\ta52_trace_id = commit->a52_trace_id;\n\tA52_R210_REC_ID(a52_trace, a52_trace_id, "dispatch enter nb=%d",\n\t\t\t  nonblock);\n''',
        'dispatch locals',
    )
    text = replace_once(
        text,
        '\t\t\t\t\tret = 0;\n',
        '\t\t\t\t\tret = 0;\n\t\t\t\t\tA52_R210_REC_ID(a52_trace, a52_trace_id,\n\t\t\t\t\t\t\t  "dispatch queued crtc=%u",\n\t\t\t\t\t\t\t  crtc->base.id);\n',
        'dispatch queued',
    )
    text = replace_once_after(
        text,
        'static void msm_atomic_commit_dispatch',
        '\tif (ret) {\n',
        '\tA52_R210_REC_ID(a52_trace, a52_trace_id, "dispatch result rc=%d", ret);\n\tif (ret) {\n',
        'dispatch result',
    )
    text = replace_once(
        text,
        '\t\tcomplete_commit(commit);\n\t} else if (!nonblock) {\n\t\tkthread_flush_work(&commit->commit_work);\n\t}\n',
        '''\t\tA52_R210_REC_ID(a52_trace, a52_trace_id, "dispatch fallback");\n\t\tcomplete_commit(commit);\n\t} else if (!nonblock) {\n\t\tA52_R210_REC_ID(a52_trace, a52_trace_id, "dispatch flush enter");\n\t\tkthread_flush_work(&commit->commit_work);\n\t\tA52_R210_REC_ID(a52_trace, a52_trace_id, "dispatch flush exit");\n\t}\n''',
        'dispatch fallback/flush',
    )

    text = replace_once(
        text,
        '\tint i, ret;\n',
        '\tint i, ret;\n\tint a52_trace_index;\n\tbool a52_trace;\n',
        'atomic trace declarations',
    )
    text = replace_once(
        text,
        '#endif\n\n\tif (!priv || priv->shutdown_in_progress) {\n',
        '''#endif\n\n\ta52_trace_index = atomic_inc_return(&a52_r210_commit_sequence);\n\ta52_trace = a52_trace_index <= A52_R210_TRACE_LIMIT;\n\tA52_R210_REC_ID(a52_trace, a52_trace_index,\n\t\t\t  "commit enter nb=%d", nonblock);\n\n\tif (!priv || priv->shutdown_in_progress) {\n\t\tA52_R210_REC_ID(a52_trace, a52_trace_index,\n\t\t\t\t  "commit reject priv=%d shutdown=%d",\n\t\t\t\t  !priv, priv ? priv->shutdown_in_progress : -1);\n''',
        'atomic entry',
    )
    text = replace_once(
        text,
        '\tret = drm_atomic_helper_prepare_planes(dev, state);\n\tif (ret) {\n',
        '\tret = drm_atomic_helper_prepare_planes(dev, state);\n\tA52_R210_REC_ID(a52_trace, a52_trace_index, "prepare_planes rc=%d", ret);\n\tif (ret) {\n',
        'prepare planes',
    )
    text = replace_once(
        text,
        '\tc = commit_init(state, nonblock);\n\tif (!c) {\n',
        '''\tc = commit_init(state, nonblock);\n\tif (c) {\n\t\tc->a52_trace = a52_trace;\n\t\tc->a52_trace_id = (u32)a52_trace_index;\n\t}\n\tA52_R210_REC_ID(a52_trace, a52_trace_index, "commit_init ok=%d", !!c);\n\tif (!c) {\n''',
        'commit init',
    )
    text = replace_once(
        text,
        '\t/* Protection for prepare_fence callback */\nretry:\n',
        '\tA52_R210_REC(c, "masks crtc=0x%x plane=0x%x",\n\t\t\tc->crtc_mask, c->plane_mask);\n\n\t/* Protection for prepare_fence callback */\nretry:\n\tA52_R210_REC(c, "conn_lock enter");\n',
        'mask and lock entry',
    )
    text = replace_once(
        text,
        '\tret = drm_modeset_lock(&state->dev->mode_config.connection_mutex,\n\t\tstate->acquire_ctx);\n\n\tif (ret == -EDEADLK) {\n',
        '''\tret = drm_modeset_lock(&state->dev->mode_config.connection_mutex,\n\t\tstate->acquire_ctx);\n\tA52_R210_REC(c, "conn_lock rc=%d", ret);\n\n\tif (ret == -EDEADLK) {\n''',
        'lock result',
    )
    text = replace_once(
        text,
        '\t/* Start Atomic */\n\tspin_lock(&priv->pending_crtcs_event.lock);\n',
        '\t/* Start Atomic */\n\tA52_R210_REC(c, "pending wait enter pc=0x%x pp=0x%x",\n\t\t\tpriv->pending_crtcs, priv->pending_planes);\n\tspin_lock(&priv->pending_crtcs_event.lock);\n',
        'pending wait entry',
    )
    text = replace_once(
        text,
        '\tspin_unlock(&priv->pending_crtcs_event.lock);\n\n\tif (ret)\n',
        '\tspin_unlock(&priv->pending_crtcs_event.lock);\n\tA52_R210_REC(c, "pending wait exit rc=%d", ret);\n\n\tif (ret)\n',
        'pending wait exit',
    )
    text = replace_once(
        text,
        '\tWARN_ON(drm_atomic_helper_swap_state(state, false) < 0);\n',
        '\tA52_R210_REC(c, "swap_state enter");\n\tWARN_ON(drm_atomic_helper_swap_state(state, false) < 0);\n\tA52_R210_REC(c, "swap_state exit");\n',
        'swap state',
    )
    text = replace_once(
        text,
        '\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->prepare_fence)\n\t\tpriv->kms->funcs->prepare_fence(priv->kms, state);\n',
        '''\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->prepare_fence) {\n\t\tA52_R210_REC(c, "prepare_fence enter");\n\t\tpriv->kms->funcs->prepare_fence(priv->kms, state);\n\t\tA52_R210_REC(c, "prepare_fence exit");\n\t}\n''',
        'prepare fence',
    )
    text = replace_once(
        text,
        '\tdrm_atomic_state_get(state);\n\tmsm_atomic_commit_dispatch(dev, state, c);\n\n\tSDE_ATRACE_END("atomic_commit");\n\n\treturn 0;\n',
        '''\tdrm_atomic_state_get(state);\n\tA52_R210_REC(c, "dispatch call");\n\tmsm_atomic_commit_dispatch(dev, state, c);\n\n\tSDE_ATRACE_END("atomic_commit");\n\tA52_R210_REC_ID(a52_trace, a52_trace_index, "commit return rc=0");\n\n\treturn 0;\n''',
        'dispatch call and return',
    )
    text = replace_once(
        text,
        'err_free:\n\tkfree(c);\nerror:\n\tdrm_atomic_helper_cleanup_planes(dev, state);\n',
        '''err_free:\n\tA52_R210_REC_ID(a52_trace, a52_trace_index, "commit err_free rc=%d", ret);\n\tkfree(c);\nerror:\n\tA52_R210_REC_ID(a52_trace, a52_trace_index, "commit error rc=%d", ret);\n\tdrm_atomic_helper_cleanup_planes(dev, state);\n''',
        'atomic error paths',
    )

    required = [
        'DRMPOST 210 c=%u',
        'fences wait enter',
        'pending wait enter',
        'dispatch queued crtc=%u',
        'commit return rc=0',
    ]
    for marker in required:
        if marker not in text:
            raise RuntimeError(f'atomic marker missing: {marker}')
    return text


def apply(root: Path) -> None:
    recorder = root / RECORDER_REL
    atomic = root / ATOMIC_REL
    if not recorder.is_file() or not atomic.is_file():
        raise RuntimeError('required source files are missing')
    recorder.write_text(patch_recorder(recorder.read_text()), encoding='utf-8')
    atomic.write_text(patch_atomic(atomic.read_text()), encoding='utf-8')


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix='a52-r210-') as tmp:
        tmp_root = Path(tmp)
        (tmp_root / RECORDER_REL).parent.mkdir(parents=True, exist_ok=True)
        (tmp_root / ATOMIC_REL).parent.mkdir(parents=True, exist_ok=True)
        (tmp_root / RECORDER_REL).write_text((root / RECORDER_REL).read_text(), encoding='utf-8')
        (tmp_root / ATOMIC_REL).write_text((root / ATOMIC_REL).read_text(), encoding='utf-8')
        apply(tmp_root)
        rec = (tmp_root / RECORDER_REL).read_text()
        atom = (tmp_root / ATOMIC_REL).read_text()
        assert '#define A52_R179_RS_ROOTS 48U' in rec
        assert '#define A52_R179_DATA_BYTES 141U' in rec
        assert '#define A52_R179_PREFIX "R48"' in rec
        assert 'char message[A52_R210_PACKED_MESSAGE_LEN];' in rec
        assert 'DRMPOST 210 c=%u' in atom
        print('phase210 RS48 and first-atomic patcher self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('Phase210 RS48 recorder and first-atomic trace applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
