#!/usr/bin/env python3
"""Сводка охвата DexScreener: tk376-dexscreener.json -> доля vol24 наших пулов по сети и квантилям. usage: ... in.json out.csv"""
import sys, json, csv, statistics, collections
d = [o for o in json.load(open(sys.argv[1])) if o['status'] == 'ok' and o['vol_all'] > 0]
st = collections.Counter(o['status'] for o in json.load(open(sys.argv[1])))
rows = []
def q(xs, p): xs = sorted(xs); return xs[min(int(p * len(xs)), len(xs) - 1)]
for net in ('ALL', 'ethereum', 'base', 'bsc'):
    s = [o for o in d if net == 'ALL' or o['net'] == net]
    if not s: continue
    sh = [o['vol_ours'] / o['vol_all'] for o in s]
    rows.append(dict(net=net, coins=len(s), vol_all=sum(o['vol_all'] for o in s), vol_ours=sum(o['vol_ours'] for o in s),
                     share_weighted=round(sum(o['vol_ours'] for o in s) / sum(o['vol_all'] for o in s), 3),
                     share_p10=round(q(sh, .1), 3), share_p50=round(q(sh, .5), 3), share_p90=round(q(sh, .9), 3),
                     med_npools=statistics.median(o['npools'] for o in s), med_nours=statistics.median(o['nours'] for o in s)))
w = csv.DictWriter(open(sys.argv[2], 'w', newline=''), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(dict(st)); [print(r) for r in rows]
