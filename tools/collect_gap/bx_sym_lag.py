#!/usr/bin/env python3
"""BingX: лаг сделок по символу до/после смены раскладки — идёт ли лаг за символом (его состав в шарде) или за шардом (соединением).
Запуск: bx_sym_lag.py <корень> <день> <запуск_до> <запуск_после>
Берёт строки сделок `t` из <шард>-<запуск>.tsv.zst; лаг = поле 3 (мс); по символу — медиана и число записей."""
import collections, glob, os, statistics, subprocess, sys

root, day, run_b, run_a = sys.argv[1:5]

def load(run):
    out = {}  # sym -> (shard, Counter(lag))
    for f in sorted(glob.glob(os.path.join(root, day, f'trade0*-{run}.tsv.zst'))):
        shard = os.path.basename(f).split('-')[0]
        p = subprocess.Popen(['zstd', '-dc', f], stdout=subprocess.PIPE)
        for ln in p.stdout:
            q = ln.split(b'\t')
            if len(q) < 8 or q[0] != b't':
                continue
            try:
                lag = int(float(q[2]))
            except ValueError:
                continue
            sym = q[4]
            e = out.get(sym)
            if e is None:
                e = out[sym] = [shard, collections.Counter()]
            e[1][lag] += 1
        p.wait()
    return out

def med(c):
    n = sum(c.values())
    k, s = n // 2, 0
    for v in sorted(c):
        s += c[v]
        if s > k:
            return v
    return None

b, a = load(run_b), load(run_a)
print('символов до', len(b), 'после', len(a))
rows = []
for sym, (sh_a, ca) in a.items():
    if sym in b and sum(ca.values()) >= 200 and sum(b[sym][1].values()) >= 200:
        rows.append((sym, b[sym][0], med(b[sym][1]), sum(b[sym][1].values()), sh_a, med(ca), sum(ca.values())))
print('символов в обоих запусках (>=200 записей) ', len(rows))
by_after = collections.defaultdict(list)
by_before = collections.defaultdict(list)
for sym, shb, lb, nb, sha, la, na in rows:
    by_after[sha].append((la - lb, na, lb, la))
    by_before[shb].append((la - lb, na, lb, la))
def wmed(lst, i):
    v = sorted((x[i], x[1]) for x in lst)
    tot = sum(w for _, w in v)
    s = 0
    for x, w in v:
        s += w
        if s * 2 >= tot:
            return x
print('группа по шарду ПОСЛЕ: симв., записей после, взв.медиана лаг до / после / дельта')
for sh in sorted(by_after):
    l = by_after[sh]
    print('  %s  %4d  %9d  %5.0f / %5.0f / %+5.0f' % (sh, len(l), sum(x[1] for x in l), wmed(l, 2), wmed(l, 3), wmed(l, 0)))
print('группа по шарду ДО:')
for sh in sorted(by_before):
    l = by_before[sh]
    print('  %s  %4d  %9d  %5.0f / %5.0f / %+5.0f' % (sh, len(l), sum(x[1] for x in l), wmed(l, 2), wmed(l, 3), wmed(l, 0)))
# лаг по символу устойчив ли: корреляция до/после (по символам)
x = [r[2] for r in rows]
y = [r[5] for r in rows]
n = len(x)
mx, my = sum(x) / n, sum(y) / n
cov = sum((p - mx) * (q - my) for p, q in zip(x, y))
print('Pearson(лаг символа до, после) = %.3f' % (cov / (sum((p - mx) ** 2 for p in x) ** .5 * sum((q - my) ** 2 for q in y) ** .5)))
# дельта в зависимости от смены шарда
same = [r for r in rows if r[1] == r[4]]
chg = [r for r in rows if r[1] != r[4]]
print('символы, оставшиеся в том же шарде:', len(same), ' сменившие шард:', len(chg))
# число сделок/с символа (вес) против лага символа после
top = sorted(a.items(), key=lambda kv: -sum(kv[1][1].values()))[:10]
print('10 самых частых символов после: шард, записей, медиана лага (до: шард, медиана)')
for sym, (sh, c) in top:
    bb = b.get(sym)
    print('  %-22s %s %8d %4s   до: %s %s' % (sym.decode(), sh, sum(c.values()), med(c), bb[0] if bb else '-', med(bb[1]) if bb else '-'))
