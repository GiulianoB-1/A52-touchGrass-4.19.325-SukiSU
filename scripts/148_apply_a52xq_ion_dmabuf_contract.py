#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

ION_DMABUF_REL = Path('drivers/staging/android/ion/ion_dma_buf.c')
QSEE_REL = Path('drivers/a52_secure/qseecom.c')
REPORT = 'phase22-ion-dmabuf-contract-report.json'
MARKER = 'A52_ION_DMABUF_FLAGS_FALLBACK'
TRACE_MARKER = 'A52_QSEECOM_DMABUF_SHAPE_TRACE'

GET_FLAGS_RE = re.compile(
    r'''static int ion_dma_buf_get_flags\(struct dma_buf \*dmabuf, unsigned long \*flags\)\n'''
    r'''\{\n'''
    r'''\tstruct ion_buffer \*buffer = dmabuf->priv;\n'''
    r'''\tstruct ion_heap \*heap = buffer->heap;\n\n'''
    r'''\tif \(!heap->buf_ops\.get_flags\)\n'''
    r'''\t\treturn -EOPNOTSUPP;\n\n'''
    r'''\treturn heap->buf_ops\.get_flags\(dmabuf, flags\);\n'''
    r'''\}'''
)

GET_FLAGS_REPLACEMENT = '''/* A52_ION_DMABUF_FLAGS_FALLBACK
 * Downstream Qualcomm ION always exposes buffer->flags through
 * dma_buf_get_flags(). ACK delegates this to optional heap-specific ops,
 * so the generic system heap otherwise returns -EOPNOTSUPP. Preserve a
 * heap override when one exists and use the downstream core fallback.
 */
static int ion_dma_buf_get_flags(struct dma_buf *dmabuf, unsigned long *flags)
{
\tstruct ion_buffer *buffer = dmabuf->priv;
\tstruct ion_heap *heap = buffer->heap;

\tif (heap->buf_ops.get_flags)
\t\treturn heap->buf_ops.get_flags(dmabuf, flags);

\t*flags = buffer->flags;
\treturn 0;
}'''

CACHE_FLAGS_OLD = '''\tret = dma_buf_get_flags(dmabuf, &flags);\n\tif (ret) {'''
CACHE_FLAGS_NEW = '''\tret = dma_buf_get_flags(dmabuf, &flags);\n\ta52_ackfr_record("DMABUF flags cache ret=%d flags=%lx", ret, flags);\n\tif (ret) {'''

BRIDGE_FLAGS_OLD = '''\tret = dma_buf_get_flags(dmabuf, &dma_buf_flags);\n\tif (ret) {'''
BRIDGE_FLAGS_NEW = '''\tret = dma_buf_get_flags(dmabuf, &dma_buf_flags);\n\ta52_ackfr_record("DMABUF flags bridge fd=%d ret=%d flags=%lx n=%u",\n\t\tion_fd, ret, dma_buf_flags, sgt ? sgt->nents : 0);\n\tif (ret) {'''

MAP_OLD = '''\tnew_sgt = dma_buf_map_attachment(new_attach, DMA_BIDIRECTIONAL);\n\tif (IS_ERR_OR_NULL(new_sgt)) {'''
MAP_NEW = '''\tnew_sgt = dma_buf_map_attachment(new_attach, DMA_BIDIRECTIONAL);\n\tif (!IS_ERR_OR_NULL(new_sgt)) {\n\t\tstruct scatterlist *a52_sg;\n\t\tsize_t a52_total = 0;\n\t\tunsigned int a52_i;\n\n\t\tfor_each_sg(new_sgt->sgl, a52_sg, new_sgt->nents, a52_i)\n\t\t\ta52_total += a52_sg->length;\n\t\ta52_ackfr_record(\n\t\t\t"DMABUF shape fd=%d buf=%zu n=%u orig=%u",\n\t\t\tion_fd, new_dma_buf->size, new_sgt->nents,\n\t\t\tnew_sgt->orig_nents);\n\t\ta52_ackfr_record(\n\t\t\t"DMABUF first fd=%d len=%u dma_len=%u total=%zu",\n\t\t\tion_fd, new_sgt->sgl->length,\n\t\t\tsg_dma_len(new_sgt->sgl), a52_total);\n\t\ta52_ackfr_record(\n\t\t\t"DMABUF addr fd=%d dma=%llx phys=%llx", ion_fd,\n\t\t\t(unsigned long long)sg_dma_address(new_sgt->sgl),\n\t\t\t(unsigned long long)sg_phys(new_sgt->sgl));\n\t}\n\tif (IS_ERR_OR_NULL(new_sgt)) {'''


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, str]:
    if new in text:
        if text.count(new) != 1:
            raise SystemExit(f'{label}: staged replacement count is not one')
        return text, 'already-present'
    if text.count(old) != 1:
        raise SystemExit(f'{label}: anchor count expected 1, found {text.count(old)}')
    return text.replace(old, new, 1), 'inserted'


def patch_ion(path: Path) -> dict[str, object]:
    text = read(path)
    if MARKER in text:
        if text.count(MARKER) != 1:
            raise SystemExit('ION dma-buf flags fallback marker count is not one')
        state = 'already-present'
    else:
        matches = list(GET_FLAGS_RE.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                f'ION dma-buf get_flags anchor expected 1, found {len(matches)}'
            )
        match = matches[0]
        text = text[:match.start()] + GET_FLAGS_REPLACEMENT + text[match.end():]
        state = 'inserted'
    required = (
        MARKER,
        'if (heap->buf_ops.get_flags)',
        '*flags = buffer->flags;',
        'return 0;',
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit('ION dma-buf fallback audit failed: ' + ', '.join(missing))
    write(path, text)
    return {
        'source': str(ION_DMABUF_REL),
        'state': state,
        'heap_override_preserved': True,
        'default_returns_buffer_flags': True,
    }


def patch_qsee(path: Path) -> dict[str, object]:
    text = read(path)
    text, cache_state = replace_once(
        text, CACHE_FLAGS_OLD, CACHE_FLAGS_NEW, 'QSEECOM cache flags trace'
    )
    text, bridge_state = replace_once(
        text, BRIDGE_FLAGS_OLD, BRIDGE_FLAGS_NEW, 'QSEECOM bridge flags trace'
    )
    text, map_state = replace_once(
        text, MAP_OLD, MAP_NEW, 'QSEECOM dma-buf shape trace'
    )
    if TRACE_MARKER not in text:
        include_anchor = '#include <soc/qcom/qtee_shmbridge.h>\n'
        if text.count(include_anchor) != 1:
            raise SystemExit('QSEECOM trace marker include anchor mismatch')
        text = text.replace(
            include_anchor,
            include_anchor + '\n/* ' + TRACE_MARKER + ' */\n',
            1,
        )
    required = (
        TRACE_MARKER,
        'DMABUF flags cache ret=%d flags=%lx',
        'DMABUF flags bridge fd=%d ret=%d flags=%lx n=%u',
        'DMABUF shape fd=%d buf=%zu n=%u orig=%u',
        'DMABUF first fd=%d len=%u dma_len=%u total=%zu',
        'DMABUF addr fd=%d dma=%llx phys=%llx',
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit('QSEECOM dma-buf trace audit failed: ' + ', '.join(missing))
    write(path, text)
    return {
        'source': str(QSEE_REL),
        'cache_flags_trace': cache_state,
        'bridge_flags_trace': bridge_state,
        'shape_trace': map_state,
        'payload_capture': False,
    }


def self_test() -> None:
    ion_sample = '''static int ion_dma_buf_get_flags(struct dma_buf *dmabuf, unsigned long *flags)\n{\n\tstruct ion_buffer *buffer = dmabuf->priv;\n\tstruct ion_heap *heap = buffer->heap;\n\n\tif (!heap->buf_ops.get_flags)\n\t\treturn -EOPNOTSUPP;\n\n\treturn heap->buf_ops.get_flags(dmabuf, flags);\n}\n'''
    qsee_sample = '''#include <soc/qcom/qtee_shmbridge.h>\nstatic int cache(struct dma_buf *dmabuf)\n{\n\tint ret; unsigned long flags = 0;\n\tret = dma_buf_get_flags(dmabuf, &flags);\n\tif (ret) {\n\t\treturn ret;\n\t}\n\treturn 0;\n}\nstatic int bridge(int ion_fd, struct dma_buf *dmabuf, struct sg_table *sgt)\n{\n\tint ret; unsigned long dma_buf_flags = 0;\n\tret = dma_buf_get_flags(dmabuf, &dma_buf_flags);\n\tif (ret) {\n\t\treturn ret;\n\t}\n\treturn 0;\n}\nstatic int map(int ion_fd, struct dma_buf_attachment *new_attach, struct dma_buf *new_dma_buf)\n{\n\tstruct sg_table *new_sgt;\n\tnew_sgt = dma_buf_map_attachment(new_attach, DMA_BIDIRECTIONAL);\n\tif (IS_ERR_OR_NULL(new_sgt)) {\n\t\treturn -1;\n\t}\n\treturn 0;\n}\n'''
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ion = root / ION_DMABUF_REL
        qsee = root / QSEE_REL
        ion.parent.mkdir(parents=True, exist_ok=True)
        qsee.parent.mkdir(parents=True, exist_ok=True)
        ion.write_text(ion_sample, encoding='utf-8')
        qsee.write_text(qsee_sample, encoding='utf-8')
        first_ion = patch_ion(ion)
        second_ion = patch_ion(ion)
        first_qsee = patch_qsee(qsee)
        second_qsee = patch_qsee(qsee)
        if first_ion['state'] != 'inserted' or second_ion['state'] != 'already-present':
            raise SystemExit('ION dma-buf fallback self-test failed')
        if first_qsee['shape_trace'] != 'inserted' or second_qsee['shape_trace'] != 'already-present':
            raise SystemExit('QSEECOM dma-buf trace self-test failed')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    ion_path = root / ION_DMABUF_REL
    qsee_path = root / QSEE_REL
    missing = [str(path) for path in (ion_path, qsee_path) if not path.is_file()]
    if missing:
        raise SystemExit('missing staged dma-buf contract sources: ' + ', '.join(missing))

    report = {
        'status': 'ion-dmabuf-contract-compat-staged',
        'hardware_validated': False,
        'payload_capture': False,
        'observed_risk': {
            'downstream_get_flags': 'returns ion_buffer.flags',
            'ack_default_get_flags': '-EOPNOTSUPP without heap override',
            'qseecom_behavior': 'treats dma_buf_get_flags failure as fatal',
        },
        'ion': patch_ion(ion_path),
        'qseecom': patch_qsee(qsee_path),
        'deferred_findings': {
            'dma_buf_destructor': 'ACK compatibility macro is currently a no-op',
            'secure_heap_parity': 'requires observed flags and heap masks before translation',
        },
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
