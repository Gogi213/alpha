#!/usr/bin/env python3
"""TK-380: part-*.jsonl SQD (EVM) -> swaps.csv.gz (сырые amount0/amount1 со знаком: + в пул, − из пула) + summary.csv (пул × сутки).
Использование: tk380-sqd-parse.py <dir с part-*.jsonl> <outdir>"""
import csv, glob, gzip, json, os, sys, collections
src, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
V2 = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'
def s256(h):
    v = int(h, 16); return v - (1 << 256) if v >> 255 else v
summ = collections.defaultdict(lambda: [0, set()]); first = last = None; nb = 0
with gzip.open(os.path.join(out, 'swaps.csv.gz'), 'wt', newline='') as g:
    w = csv.writer(g); w.writerow(['ts', 'block', 'txidx', 'logidx', 'pool', 'kind', 'amount0', 'amount1', 'sender', 'gas_price', 'gas_used', 'txhash'])
    for f in sorted(glob.glob(os.path.join(src, 'part-*.jsonl'))):
        for line in open(f, encoding='utf-8'):
            r = json.loads(line); h = r['header']; ts = h['timestamp']; nb += 1
            first = ts if first is None else first; last = ts
            tx = {t['transactionIndex']: t for t in r.get('transactions', [])}
            for l in r.get('logs', []):
                d = l['data'][2:]; k = 'v2' if l['topics'][0] == V2 else 'v3'
                if k == 'v2':
                    a = [int(d[i * 64:(i + 1) * 64], 16) for i in range(4)]; a0, a1 = a[0] - a[2], a[1] - a[3]
                else:
                    a0, a1 = s256(d[:64]), s256(d[64:128])
                t = tx.get(l['transactionIndex'], {})
                w.writerow([ts, h['number'], l['transactionIndex'], l['logIndex'], l['address'], k, a0, a1, t.get('from', ''),
                            int(t['gasPrice'], 16) if t.get('gasPrice') else '', int(t['gasUsed'], 16) if t.get('gasUsed') else '', l['transactionHash']])
                e = summ[(l['address'], ts // 86400)]; e[0] += 1; e[1].add(t.get('from', ''))
with open(os.path.join(out, 'summary.csv'), 'w', newline='') as s:
    w = csv.writer(s); w.writerow(['pool', 'day_utc', 'swaps', 'senders'])
    for (p, d), (n, u) in sorted(summ.items()): w.writerow([p, d, n, len(u)])
json.dump({'done': True, 'first_ts': first, 'last_ts': last, 'blocks_with_logs': nb, 'pools': len({p for p, _ in summ}), 'swaps': sum(v[0] for v in summ.values())}, open(os.path.join(out, 'DONE.json'), 'w'))
