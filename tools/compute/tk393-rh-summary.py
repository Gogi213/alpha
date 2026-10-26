#!/usr/bin/env python3
"""TK-393: сводка свопов Robinhood (RPC logs.jsonl) по пуле/суткам UTC: pool,day_utc,swaps,senders (формат summary.csv tk380-sqd-parse). v4: пул = topic1 (id)."""
import collections, csv, json, sys
src, out = sys.argv[1:3]
SW4 = '0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f'
n = collections.Counter(); s = collections.defaultdict(set); seen = set()
for ln in open(src, encoding='utf-8'):
    r = json.loads(ln); k = (r['tx'], r['li'])
    if k in seen: continue   # докачка с курсора может повторить окно
    seen.add(k)
    tp = r['tp']; pool = tp[1].lower() if tp[0] == SW4 else r['a'].lower()
    snd = tp[2] if tp[0] != SW4 and len(tp) > 2 else r['tx']
    key = (pool, r['t'] // 86400); n[key] += 1; s[key].add(snd)
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f); w.writerow(['pool', 'day_utc', 'swaps', 'senders'])
    for key in sorted(n): w.writerow([key[0], key[1], n[key], len(s[key])])
print(len(seen), 'swaps', len({k[0] for k in n}), 'pools')
