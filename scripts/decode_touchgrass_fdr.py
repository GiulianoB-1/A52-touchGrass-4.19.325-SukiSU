#!/usr/bin/env python3
import argparse
import csv
import json
import struct
from pathlib import Path

HDR = struct.Struct('<8sHHHHIIQQQQ72s')
REC = struct.Struct('<QQQQQQQQiIIIIIHHHH')
SUBSYSTEMS = {
    1:'CORE',2:'DRIVER',3:'IOMMU',4:'GPU',5:'DISPLAY',6:'STORAGE',
    7:'ANDROID',8:'POWER',9:'USB',10:'NET',11:'AUDIO',12:'CAMERA',
    13:'INPUT',14:'SENSOR',15:'THERMAL',16:'SECURITY',17:'PM',
    18:'FIRMWARE',19:'MEMORY',20:'IRQ',21:'TEST',22:'META',
}
BANKS = {
    0:'core-test-meta',1:'driver-firmware',2:'iommu-memory',3:'gpu',
    4:'display-camera',5:'storage-usb-net',6:'android-io',
    7:'power-pm-thermal-irq',
}
COMMIT = 0xA52F
MARK_EVENT = 0x4D41524B
OBJ_EVENT = 0x4F424A01


def load_dictionary(path):
    if not path:
        return {}
    obj = json.loads(Path(path).read_text())
    if isinstance(obj, dict) and 'events' in obj:
        obj = obj['events']
    return {int(k, 0) if isinstance(k, str) and k.startswith('0x') else int(k): v
            for k, v in obj.items()}


def marker_text(a, b, c, d):
    raw = struct.pack('<QQQQ', a, b, c, d)
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stream')
    ap.add_argument('--dictionary')
    ap.add_argument('--out', default='tg_fdr_decoded')
    args = ap.parse_args()

    data = Path(args.stream).read_bytes()
    if len(data) < HDR.size:
        raise SystemExit('stream is smaller than FDR header')
    h = HDR.unpack_from(data, 0)
    magic = h[0].rstrip(b'\0')
    if magic != b'TGFDR1':
        raise SystemExit(f'bad magic: {magic!r}')
    version, hsize, rsize, bank_count, session, capacity = h[1:7]
    if hsize != HDR.size or rsize != REC.size:
        raise SystemExit(f'unsupported sizes header={hsize} record={rsize}')

    dictionary = load_dictionary(args.dictionary)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    events = []
    off = hsize
    bad_commit = 0

    while off + rsize <= len(data):
        x = REC.unpack_from(data, off)
        off += rsize
        (seq, bank_seq, ns, obj, a, b, c, d, rc, pid, tid, flags,
         rec_session, event, cpu, subsys, bank, commit) = x
        if commit != COMMIT:
            bad_commit += 1
            continue
        tag = dictionary.get(event)
        if event == MARK_EVENT:
            tag = 'TEST:MARKER'
        elif event == OBJ_EVENT:
            tag = 'META:OBJECT_REGISTER'
        events.append({
            'seq': seq, 'bank_seq': bank_seq, 'ns': ns, 'seconds': ns / 1e9,
            'session': rec_session, 'cpu': cpu, 'pid': pid, 'tid': tid,
            'subsystem_id': subsys,
            'subsystem': SUBSYSTEMS.get(subsys, f'SUBSYS_{subsys}'),
            'bank': bank, 'bank_name': BANKS.get(bank, f'bank-{bank}'),
            'event_id': event, 'event': tag or f'0x{event:08x}',
            'object_id': obj, 'rc': rc, 'flags': flags,
            'a': a, 'b': b, 'c': c, 'd': d,
            'marker': marker_text(a, b, c, d) if event == MARK_EVENT else '',
        })

    events.sort(key=lambda e: e['seq'])
    fields = list(events[0].keys()) if events else [
        'seq','bank_seq','ns','seconds','session','cpu','pid','tid',
        'subsystem_id','subsystem','bank','bank_name','event_id','event',
        'object_id','rc','flags','a','b','c','d','marker'
    ]
    with (outdir / 'events.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(events)
    (outdir / 'events.json').write_text(json.dumps(events, indent=2))

    gaps = []
    for aev, bev in zip(events, events[1:]):
        if bev['seq'] > aev['seq'] + 1:
            gaps.append({'after': aev['seq'], 'before': bev['seq'],
                         'missing': bev['seq'] - aev['seq'] - 1})

    summary = {
        'format': 'TGFDR1', 'version': version, 'session': session,
        'record_size': rsize, 'bank_count': bank_count,
        'capacity_per_bank': capacity, 'header_open_ns': h[7],
        'header_first_global': h[8], 'header_current_global': h[9],
        'header_reader_lost': h[10], 'decoded_records': len(events),
        'bad_commit_records': bad_commit,
        'trailing_bytes': len(data) - off,
        'first_seq': events[0]['seq'] if events else None,
        'last_seq': events[-1]['seq'] if events else None,
        'sequence_gap_count': len(gaps),
        'sequence_gaps_total': sum(g['missing'] for g in gaps),
        'sequence_gaps': gaps[:1000],
        'by_subsystem': {}, 'by_bank': {},
        'markers': [{'seq': e['seq'], 'seconds': e['seconds'], 'marker': e['marker']}
                    for e in events if e['marker']],
    }
    for e in events:
        summary['by_subsystem'][e['subsystem']] = summary['by_subsystem'].get(e['subsystem'], 0) + 1
        summary['by_bank'][e['bank_name']] = summary['by_bank'].get(e['bank_name'], 0) + 1
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
