#!/usr/bin/env python3
"""TK-386 Д8: нетто эпизодов по возрасту цены DEX на входе (корзины age: 0-10 с | 20-30 с | 40-60 с). Вход: каталоги с episodes.csv; выход: csv в stdout."""
import sys, csv, statistics
rows = []
for d in sys.argv[1:]:
    tag = d.rstrip('/').split('/')[-1]
    for r in csv.DictReader(open(f'{d}/episodes.csv')):
        if r['age'] == '': continue
        rows.append((tag, r['coin'], int(r['age']), float(r['net']), float(r['ctrl']) if r['ctrl'] else None, r['conv']))
def bk(a): return '0-10с' if a <= 10 else ('20-30с' if a <= 30 else '40-60с')
w = csv.writer(sys.stdout); w.writerow(['run', 'coin', 'age', 'n', 'net_mean_bp', 'ctrl_mean_bp', 'conv_share'])
g = {}
for t, c, a, n, ct, cv in rows: g.setdefault((t, c, bk(a)), []).append((n, ct, cv))
for (t, c, b), v in sorted(g.items()):
    cs = [x[1] for x in v if x[1] is not None]
    w.writerow([t, c, b, len(v), round(statistics.mean(x[0] for x in v), 1), round(statistics.mean(cs), 1) if cs else '', round(sum(x[2] == '1' for x in v) / len(v), 2)])
