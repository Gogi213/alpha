#!/usr/bin/env python3
"""TK-394 П-28 п.0: причина непокрытия BBO Gate (DOOD, сутки). Доля сделок в [бид;аск] при сдвиге времени сделки
на -3..+3 с (смещение часов?), промахи по стороне/расстоянию в бп, и доля попаданий на цену ЛЮБОГО уровня книги (призрачные/устаревшие уровни).
Запуск: tk394-why.py COIN DAY -> stdout JSON"""
import sys, os, json, gzip, glob, importlib.util
import numpy as np
D = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('cand', os.path.join(D, 'tk394-cand.py')); C = importlib.util.module_from_spec(sp); sp.loader.exec_module(C)
coin, day = sys.argv[1], sys.argv[2]
out = []
for f in sorted(glob.glob(f'{C.OB}/{coin}_USDT-*.csv.gz')):
    if day.replace('-', '') not in f: continue
    C.hour(f, out)
out.sort(key=lambda r: r[0])
a = np.array([(r[0], np.nan if r[1] is None else r[1], r[2], np.nan if r[3] is None else r[3], r[4]) for r in out], dtype=float)
bins = a[:, 0].astype(np.int64)
tr = []
for line in gzip.open(f'{C.TR}/{coin}_USDT/{day}.csv.gz', 'rt'):
    x = line.split(',')
    if x[0] == 'id': continue
    tr.append((int(float(x[3]) * 10), float(x[5]), float(x[4]) if len(x) > 4 else 0.0))
tr = np.array(tr)
res = {'coin': coin, 'day': day, 'trades': len(tr), 'bbo_rows': len(a), 'bin_first': int(bins[0]), 'bin_last': int(bins[-1]), 'tr_first': int(tr[0, 0]), 'tr_last': int(tr[-1, 0])}
def cov(shift):
    j = np.searchsorted(bins, tr[:, 0] + shift, side='left') - 1
    m = (j >= 0) & np.isfinite(a[np.clip(j, 0, None), 1]) & np.isfinite(a[np.clip(j, 0, None), 3])
    jj = j[m]; p = tr[m, 1]
    ins = (a[jj, 1] <= p) & (p <= a[jj, 3])
    return float(ins.mean()), jj, p, ins
res['shift'] = {str(s / 10): cov(s)[0] for s in (-30, -20, -10, -5, -2, 0, 2, 5, 10, 20, 30)}
c0, jj, p, ins = cov(0)
miss = ~ins
mid = (a[jj, 1] + a[jj, 3]) / 2
below = miss & (p < a[jj, 1]); above = miss & (p > a[jj, 3])
res['miss_below_bid'] = float(below.mean()); res['miss_above_ask'] = float(above.mean())
dist = np.where(below, (a[jj, 1] - p), (p - a[jj, 3])) / mid * 1e4
res['miss_dist_bp_q'] = {q: float(np.quantile(dist[miss], q)) for q in (.1, .5, .9, .99)} if miss.any() else None
spr = (a[jj, 3] - a[jj, 1]) / mid * 1e4
res['spread_bp_median'] = float(np.median(spr)); res['spread_bp_p90'] = float(np.quantile(spr, .9))
res['bbo_changes_per_s'] = float(len(a) / ((bins[-1] - bins[0]) / 10))
print(json.dumps(res))
