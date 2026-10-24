#!/usr/bin/env python3
"""TK-386: бутстрап по суткам (кластер) для net и net-ctrl по ячейкам эпизодов tk386-*.csv. Запуск: tk386-boot.py out.csv in1.csv ..."""
import sys, csv, glob, random, statistics, collections
rng = random.Random(386); out = sys.argv[1]; rows = []
for f in sys.argv[2:]:
    tag = f.split('tk386-')[1].rsplit('-2026', 0)[0].replace('-2026-10-24.csv', '')
    for r in csv.DictReader(open(f)):
        if r['ctrl'] == '': continue
        r['set'] = tag; rows.append(r)
cells = collections.defaultdict(list)
for r in rows: cells[(r['set'], r['coin'], r['form'], r['T'], r['ex'], r['H'])].append(r)
def ci(vals_by_day):
    days = list(vals_by_day); m = []
    for _ in range(2000):
        s = []; [s.extend(vals_by_day[rng.choice(days)]) for _ in days]
        m.append(sum(s) / len(s))
    m.sort(); return m[50], m[1949]
res = []
for k, v in sorted(cells.items()):
    if len(v) < 10: continue
    byd = collections.defaultdict(list)
    for r in v: byd[r['day']].append(float(r['net']) - float(r['ctrl']))
    if len(byd) < 3: continue
    lo, hi = ci(byd); d = [float(r['net']) - float(r['ctrl']) for r in v]
    res.append(dict(set=k[0], coin=k[1], form=k[2], T=k[3], ex=k[4], H=k[5], n=len(v), days=len(byd), net=round(statistics.mean(float(r['net']) for r in v), 1), ctrl=round(statistics.mean(float(r['ctrl']) for r in v), 1), edge=round(sum(d) / len(d), 1), lo=round(lo, 1), hi=round(hi, 1), net_pos=int(statistics.mean(float(r['net']) for r in v) > 0)))
w = csv.DictWriter(open(out, 'w', newline=''), list(res[0])); w.writeheader(); w.writerows(res)
pos = [r for r in res if r['lo'] > 0]
print('ячеек n>=10 и >=3 суток:', len(res), '| с нижней границей edge>0:', len(pos), '| net>0:', sum(r['net_pos'] for r in res))
for r in pos[:20]: print(r)
