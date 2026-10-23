#!/usr/bin/env python3
"""TK-377 шаг 4: разбивка по суткам (day) из episodes-all.csv: сверх контроля (pnl3600 - ctl_gm), net тейкер. Запуск: tk377-byday.py EPISODES.csv OUT.csv"""
import sys, csv, collections
rows = list(csv.DictReader(open(sys.argv[1], encoding='utf8'))); FEE = 15.0
out = []
for cls in ('с тяжеловесами', 'без'):
    for X in (50, 100):
        for sel in ('все', 'чистые'):
            for day in sorted({int(r['day']) for r in rows}):
                rs = [r for r in rows if r['cls'] == cls and int(r['X']) == X and int(r['day']) == day]
                if sel == 'чистые': rs = [r for r in rs if int(r['gate_age']) <= 60 and 0 <= int(r['dex_age']) <= 60]
                ex = [float(r['pnl3600']) - float(r['ctl_gm']) for r in rs if r['ctl_gm'] != '']
                if not rs: continue
                out.append(dict(cls=cls, X_pct=X / 100, sel=sel, day=day, episodes=len(rs), coins=len({r['coin'] for r in rs}),
                                pnl3600=round(sum(float(r['pnl3600']) for r in rs) / len(rs), 1),
                                excess=round(sum(ex) / len(ex), 1) if ex else '', net_taker=round(sum(float(r['pnl3600']) for r in rs) / len(rs) - FEE, 1)))
w = csv.DictWriter(open(sys.argv[2], 'w', newline='', encoding='utf8'), list(out[0])); w.writeheader(); w.writerows(out)
sys.stdout.reconfigure(encoding='utf8')
g = collections.defaultdict(list)
for o in out: g[(o['cls'], o['X_pct'], o['sel'])].append(o)
for k, v in g.items():
    print(k, 'сутки с excess>0:', sum(1 for o in v if o['excess'] != '' and o['excess'] > 0), '/', len(v), 'net>0:', sum(1 for o in v if o['net_taker'] > 0), [ (o['day'], o['excess'], o['episodes']) for o in v])
