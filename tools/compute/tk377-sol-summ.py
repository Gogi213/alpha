# Свод Solana-таблицы TK-377 из episodes.csv: без TQQQX, excess = pnl - ctl (ctl пуст — исключён),
# среднее по монето-суткам, SE по монето-суткам. Вывод — единственный источник чисел в md.
import csv, sys, math, statistics, collections
P = sys.argv[1] if len(sys.argv) > 1 else 'docs/findings/tk377-sol-episodes-2026-10-23.csv'
rows = [r for r in csv.DictReader(open(P)) if r['coin'] != 'TQQQX']
for X in sorted({int(r['X']) for r in rows}):
    rs = [r for r in rows if int(r['X']) == X]
    g = collections.defaultdict(list)
    n_all = len(rs); n_ctl = 0
    for r in rs:
        if r['ctl'] == '': continue
        n_ctl += 1
        g[(r['coin'], r['day'])].append(float(r['pnl']) - float(r['ctl']))
    m = [statistics.mean(v) for v in g.values()]
    k = len(m); mu = statistics.mean(m); se = statistics.stdev(m) / math.sqrt(k)
    pos = sum(1 for x in m if x > 0)
    net = statistics.mean(float(r['pnl']) for r in rs) - 15
    print(f'X={X/100:g}% эп.={n_all} с контролем={n_ctl} монето-суток={k} excess={mu:+.1f}±{se:.1f} z={mu/se:.1f} >0:{pos}/{k} net_тейкер(ср.эп.)={net:+.1f}')
    for c in sorted({a for a, _ in g}):
        mc = [statistics.mean(v) for (a, _), v in g.items() if a == c]
        print(f'   {c}: сут={len(mc)} excess={statistics.mean(mc):+.1f}')
